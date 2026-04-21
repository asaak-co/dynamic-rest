"""This module contains custom viewset classes."""
import csv
import re
import json
import operator as op
from decimal import Decimal
import statistics

from io import StringIO
import inflection

from django.http import QueryDict
from django.db.models import Q, Sum, Min, Max, Avg, Count, F
from django.db.models.functions import (
    Trunc, Length, Lower, Upper, Cast
)
from django.db import models
from rest_framework import exceptions, status, viewsets
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.request import is_form_media_type

from dynamic_rest.permissions import PermissionsViewSetMixin
from dynamic_rest.conf import settings
from dynamic_rest.filters import DynamicFilterBackend, DynamicSortingFilter
from dynamic_rest.metadata import DynamicMetadata
from dynamic_rest.pagination import DynamicPageNumberPagination
from dynamic_rest.processors import SideloadingProcessor
from dynamic_rest.utils import is_truthy, clean, has_joins
from dynamic_rest.condition import evaluate
from .meta import Meta


UPDATE_REQUEST_METHODS = ('PUT', 'PATCH', 'POST')
DELETE_REQUEST_METHOD = 'DELETE'

class REGEX:
    arithmetic_operator = '[/*+-]'
    arithmetic_operator_capture = '([/*+-])'
    identifier = '[a-z][ A-Za-z0-9_.]*'
    word_number = r'^([a-zA-Z]+)([0-9]+)$'
    literal = '(?:[0-9][0-9.]*|[-][0-9][0-9.]*)'
    basic = f'(?:{identifier}|{literal})'
    function_expression = fr'\s*({basic})\s*\(\s*({basic})\s*\)(?: as \s*({basic})\s*)?'
    identifier_expression = fr'\s*({basic})\s*(?: as \s*({basic})\s*)'

def percent(l, this=None):
    s = sum(l)
    return Decimal('100.0') * (this if this is not None else 0)/ s if s else None

def remove_underscore(key):
    return key.replace('_', '', 1)

def remove_underscores(items):
    return [{remove_underscore(key): value for key, value in item.items()} for item in items]

def literalize(x):
    try:
        return json.loads(x)
    except:
        return x

class QueryParams(QueryDict):
    """
    Extension of Django's QueryDict. Instantiated from a DRF Request
    object, and returns a mutable QueryDict subclass. Also adds methods that
    might be useful for our usecase.
    """

    def __init__(self, query_params, *args, **kwargs):
        if hasattr(query_params, 'urlencode'):
            query_string = query_params.urlencode()
        else:
            assert isinstance(query_params, (str, bytes))
            query_string = query_params
        kwargs['mutable'] = True
        super(QueryParams, self).__init__(query_string, *args, **kwargs)

    def add(self, key, value):
        """
        Method to accept a list of values and append to flat list.
        QueryDict.appendlist(), if given a list, will append the list,
        which creates nested lists. In most cases, we want to be able
        to pass in a list (for convenience) but have it appended into
        a flattened list.
        TODO: Possibly throw an error if add() is used on a non-list param.
        """
        if isinstance(value, list):
            for val in value:
                self.appendlist(key, val)
        else:
            self.appendlist(key, value)


class WithDynamicViewSetBase(object):

    """A viewset that can support dynamic API features.

    Attributes:
      features: A list of features supported by the viewset.
      meta: Extra data that is added to the response by the DynamicRenderer.
    """

    SET_REQUEST_ON_SAVE = settings.SET_REQUEST_ON_SAVE

    DEBUG = 'debug'
    SIDELOADING = 'sideloading'
    INCLUDE = 'include[]'
    EXCLUDE = 'exclude[]'
    FILTER = 'filter{}'
    COMBINE = 'combine.'
    SORT = 'sort[]'
    PAGE = settings.PAGE_QUERY_PARAM
    PER_PAGE = settings.PAGE_SIZE_QUERY_PARAM

    # TODO: add support for `sort{}`
    pagination_class = DynamicPageNumberPagination
    metadata_class = DynamicMetadata
    features = (DEBUG, INCLUDE, EXCLUDE, FILTER, PAGE, PER_PAGE, SORT, SIDELOADING, COMBINE)
    meta = None
    filter_backends = (DynamicFilterBackend, DynamicSortingFilter)

    def initialize_request(self, request, *args, **kargs):
        """
        Override DRF initialize_request() method to swap request.GET
        (which is aliased by request.query_params) with a mutable instance
        of QueryParams, and to convert request MergeDict to a subclass of dict
        for consistency (MergeDict is not a subclass of dict)
        """

        def handle_encodings(request):
            """
            WSGIRequest does not support Unicode values in the query string.
            WSGIRequest handling has a history of drifting behavior between
            combinations of Python versions, Django versions and DRF versions.
            Django changed its QUERY_STRING handling here:
            https://goo.gl/WThXo6. DRF 3.4.7 changed its behavior here:
            https://goo.gl/0ojIIO.
            """
            try:
                return QueryParams(request.GET)
            except UnicodeEncodeError:
                pass

            s = request.environ.get('QUERY_STRING', '')
            try:
                s = s.encode('utf-8')
            except UnicodeDecodeError:
                pass
            return QueryParams(s)

        request.GET = handle_encodings(request)
        request = super(WithDynamicViewSetBase, self).initialize_request(
            request, *args, **kargs
        )

        try:
            # Django<1.9, DRF<3.2

            # MergeDict doesn't have the same API as dict.
            # Django has deprecated MergeDict and DRF is moving away from
            # using it - thus, were comfortable replacing it with a QueryDict
            # This will allow the data property to have normal dict methods.
            from django.utils.datastructures import MergeDict

            if isinstance(request._full_data, MergeDict):
                data_as_dict = request.data.dicts[0]
                for d in request.data.dicts[1:]:
                    data_as_dict.update(d)
                request._full_data = data_as_dict
        except:  # noqa
            pass

        return request

    @property
    def actions(self):
        actions = []
        cls = self.__class__
        for name in dir(cls):
            fn = getattr(cls, name)
            action = getattr(fn, 'drest_action', None)
            if action:
                action = action.bind(self, name)
                actions.append(action)
        return actions

    def get_allowed_methods(self):
        """Returns subset of allowed methods"""
        allowed_methods = set((x.lower() for x in (self.http_method_names or ())))
        allowed = []
        if 'put' in allowed_methods:
            allowed.append('update')
        if 'post' in allowed_methods:
            allowed.append('create')
        if 'delete' in allowed_methods:
            allowed.append('delete')
        if 'get' in allowed_methods:
            allowed.append('list')
            allowed.append('read')
        return allowed

    def get_actions(self, instance=None):
        actions = self.actions
        is_list = self.is_list()
        is_detail = self.is_get()
        result = []
        for action in actions:
            if (action.on_list and is_list) or (action.on_detail and is_detail):
                if not action.when or (evaluate(action.when, {'instance': instance})):
                    result.append(action)
        return result

    def get_renderers(self):
        """Optionally block browsable/admin API rendering. """
        renderers = super(WithDynamicViewSetBase, self).get_renderers()
        blacklist = set(('admin', 'api'))
        if settings.ENABLE_BROWSABLE_API is False:
            return [r for r in renderers if r.format not in blacklist]
        else:
            return renderers

    @classmethod
    def get_url(self, pk=None):
        return self.serializer_class.get_url(pk)

    def get_success_headers(self, data):
        serializer = getattr(data, 'serializer', None)
        headers = super(WithDynamicViewSetBase, self).get_success_headers(data)
        if serializer and serializer.instance:
            headers['Location'] = serializer.get_url(
                pk=getattr(serializer.instance, 'pk', None)
            )
        return headers

    def get_view_name(self):
        serializer_class = self.get_serializer_class()
        suffix = self.suffix or ''
        if serializer_class:
            serializer = self.serializer_class()
            if suffix.lower() == 'list':
                name = serializer.get_plural_name()
            else:
                try:
                    obj = self.get_object()
                    name_field = serializer.get_name_field()
                    name = str(getattr(obj, name_field))
                except:  # noqa
                    name = serializer.get_name()
        else:
            name = self.__class__.__name__
            name = inflection.pluralize(name) if suffix.lower() == 'list' else name
        return name.title()

    def get_request_feature(self, name):
        """Parses the request for a particular feature.

        Arguments:
          name: A feature name.

        Returns:
          A feature parsed from the URL if the feature is supported, or None.
        """
        if '[]' in name:
            # array-type
            return (
                self.request.query_params.getlist(name)
                if name in self.features
                else None
            )
        elif '{}' in name:
            # object-type (keys are not consistent)
            return self._extract_object_params(name) if name in self.features else {}
        elif '.' in name:
            return self._extract_dot_params(name) if name in self.features else {}
        else:
            # single-type
            return (
                self.request.query_params.get(name) if name in self.features else None
            )

    def _extract_dot_params(self, name):
        params = self.request.query_params.lists()
        result = {}
        prefix = name[:-1]
        for param, value in params:
            if all([v == '' for v in value]):
                continue
            if param.startswith(prefix + '.') or param == prefix:
                chain = param.split('.')
                if len(chain) == 1:
                    result[''] = value
                elif len(chain) == 2:
                    result[chain[1]] = value
                else:
                    # TODO: support deeply nested like a.b.c=1
                    raise exceptions.ParseError(
                        f'"{param}" is not a well-formed combine key'
                    )
        return result

    def _extract_object_params(self, name):
        """
        Extract object params, return as dict
        """

        params = self.request.query_params.lists()
        params_map = {}
        prefix = name[:-1]
        offset = len(prefix)
        for name, value in params:
            if all([v == '' for v in value]):
                continue
            if name.startswith(prefix):
                if name.endswith('}'):
                    name = name[offset:-1]
                elif name.endswith('}[]'):
                    # strip off trailing []
                    # this fixes an Ember queryparams issue
                    name = name[offset:-3]
                else:
                    # malformed argument like:
                    # filter{foo=bar
                    raise exceptions.ParseError(
                        '"%s" is not a well-formed filter key.' % name
                    )
            else:
                continue
            params_map[name] = value

        return params_map

    def get_queryset(self, queryset=None):
        """
        Returns a queryset for this request.

        Arguments:
          queryset: Optional root-level queryset.
        """
        serializer_class = self.get_serializer_class()
        meta = getattr(
            serializer_class,
            'get_meta',
            lambda: serializer_class.Meta
        )()
        return getattr(self, 'queryset', meta.model.objects.all())

    def get_request_fields(self):
        """Parses the INCLUDE and EXCLUDE features.

        Extracts the dynamic field features from the request parameters
        into a field map that can be passed to a serializer.

        Returns:
          A nested dict mapping serializer keys to
          True (include) or False (exclude).
        """
        if hasattr(self, '_request_fields'):
            return self._request_fields

        include_fields = self.get_request_feature(self.INCLUDE)
        exclude_fields = self.get_request_feature(self.EXCLUDE)
        request_fields = {}
        for fields, include in ((include_fields, True), (exclude_fields, False)):
            if fields is None:
                continue
            for field in fields:
                field_segments = field.split('.')
                num_segments = len(field_segments)
                current_fields = request_fields
                for i, segment in enumerate(field_segments):
                    last = i == num_segments - 1
                    if segment:
                        if last:
                            current_fields[segment] = include
                        else:
                            if segment not in current_fields:
                                current_fields[segment] = {}
                            current_fields = current_fields[segment]
                    elif not last:
                        # empty segment must be the last segment
                        raise exceptions.ParseError(
                            '"%s" is not a valid field.' % field
                        )

        self._request_fields = request_fields
        return request_fields

    def get_request_debug(self):
        debug = self.get_request_feature(self.DEBUG)
        return is_truthy(debug) if debug is not None else None

    def get_request_sideloading(self):
        sideloading = self.get_request_feature(self.SIDELOADING)
        return is_truthy(sideloading) if sideloading is not None else None

    def is_create(self):
        if self.request and self.request.method.upper() == 'POST':
            return True
        else:
            return False

    def is_update(self):
        if self.request and self.request.method.upper() in UPDATE_REQUEST_METHODS:
            return True
        else:
            return False

    def get_pk(self):
        pk = None
        if self.is_get():
            pk = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        return pk

    def is_get(self):
        if (
            self.request
            and self.request.method.upper() == 'GET'
            and (self.lookup_url_kwarg or self.lookup_field) in self.kwargs
        ):
            return True
        return False

    def is_list(self):
        if (
            self.request
            and self.request.method.upper() == 'GET'
            and (self.lookup_url_kwarg or self.lookup_field) not in self.kwargs
        ):
            return True
        return False

    def is_delete(self):
        if self.request and self.request.method.upper() == DELETE_REQUEST_METHOD:
            return True
        else:
            return False

    def get_format(self):
        if self.request and self.request.accepted_renderer:
            return self.request.accepted_renderer.format
        return None

    def get_serializer(self, *args, **kwargs):
        list_fields = None
        if self.is_list():
            list_fields = getattr(self.serializer_class.get_meta(), 'list_fields', None)
            kwargs['many'] = True
        if 'request_fields' not in kwargs:
            kwargs['request_fields'] = self.get_request_fields()
        if 'sideloading' not in kwargs:
            kwargs['sideloading'] = self.get_request_sideloading()
        if 'debug' not in kwargs:
            kwargs['debug'] = self.get_request_debug() or settings.DEBUG
        if 'envelope' not in kwargs:
            kwargs['envelope'] = True
        if list_fields and not kwargs['request_fields']:
            # default to list
            kwargs['only_fields'] = list_fields
        if settings.ALL_FIELDS_ON_UPDATE:
            if self.is_update():
                kwargs['include_fields'] = '*'
        serializer = super(WithDynamicViewSetBase, self).get_serializer(*args, **kwargs)
        if hasattr(serializer, 'initialized'):
            serializer.initialized()
        return serializer

    def paginate_queryset(self, *args, **kwargs):
        if self.PAGE in self.features:
            # make sure pagination is enabled
            if (
                self.PER_PAGE not in self.features
                and self.PER_PAGE in self.request.query_params
            ):
                # remove per_page if it is disabled
                self.request.query_params[self.PER_PAGE] = None
            return super(WithDynamicViewSetBase, self).paginate_queryset(
                *args, **kwargs
            )
        return None

    def _prefix_inex_params(self, request, feature, prefix):
        values = self.get_request_feature(feature)
        if not values:
            return
        del request.query_params[feature]
        request.query_params.add(feature, [prefix + val for val in values])

    def _refresh_query_params(self):
        if hasattr(self, '_request_fields'):
            del self._request_fields

    def create_related(self, request, pk=None, field_name=None):
        """Create an instance of a related object through a related field.

        This is only possible if:
        - The user has permission to create on the given serializer, AND
        - The related field has a source

        The signature of the endpoint is taken from the serializer, except the
        inverse field is filled with the PK value of the current record.
        """

        primary_serializer = self.get_serializer(include_fields='*')
        instance = self.get_queryset().distinct().get(pk=pk)
        related_field = primary_serializer.fields.get(field_name)
        if not related_field:
            raise exceptions.ValidationError('"%s" is not a valid field' % field_name)

        model_field = getattr(related_field, 'model_field', None)
        if not model_field:
            raise exceptions.ValidationError(
                '"%s" is not a model-bound field' % field_name
            )

        related_serializer = related_field.serializer
        related_serializer_name = related_serializer.get_name()
        remote_field = model_field.remote_field
        update_after = True

        if remote_field.null and not related_field.inverse:
            # use the hybrid API method

            related_serializer = related_field.get_serializer(
                data=request.data,
                request_fields=None,
                include_fields='*',
                envelope=True,
                many=False,
            )
        else:
            # use the full API method (must explicitly define an inverse field)
            update_after = False
            inverse_field_name = related_field.get_inverse_field_name()
            if inverse_field_name:
                # save by setting the inverse field
                inverse_field = related_serializer.get_field(inverse_field_name)
                data = request.data
                if hasattr(data, '_mutable'):
                    data._mutable = True

                keys = list(data.keys())
                if len(keys) == 1 and keys[0] == related_serializer_name:
                    data = data[related_serializer_name]

                # set the current record as the related object
                # using the inverse field
                data[inverse_field_name] = [pk] if inverse_field.many else pk
                # make sure the inverse field is included
                related_serializer = related_field.get_serializer(
                    data=data,
                    request_fields=None,
                    include_fields='*',
                    envelope=True,
                    many=False,
                )
                # set the inverse field to allow writes
                inverse_field = related_serializer.fields.get(inverse_field_name)
                inverse_field.read_only = False

            else:
                raise exceptions.ValidationError(
                    '"%s" has no inverse field' % field_name
                )

        related_serializer.initialized()
        related_serializer.is_valid(raise_exception=True)
        self.perform_create(related_serializer)

        if update_after:
            related_instance = related_serializer.instance
            if model_field.one_to_many:
                setattr(related_instance, remote_field.name, instance)
                related_instance.save()
            elif model_field.many_to_many:
                getattr(instance, model_field.name).add(related_instance)
            else:  # o2o or m2o
                setattr(instance, model_field.name, related_instance)
                instance.save()

        headers = self.get_success_headers(related_serializer.data)
        headers['Location'] = primary_serializer.get_url(pk)
        return Response(related_serializer.data, status=201, headers=headers)

    def list(self, request, **kwargs):
        combine = self.get_request_feature(self.COMBINE)
        if combine:
            return self.combine(request, combine, **kwargs)
        return super(WithDynamicViewSetBase, self).list(request, **kwargs)

    def _compute_bucket_function(self, model_field, queryset=None):
        if model_field is None:
            return None
        if queryset is None:
            queryset = self.filter_queryset(self.get_queryset())
        aggs = queryset.aggregate(_min=Min(model_field), _max=Max(model_field))
        min_value = aggs['_min']
        max_value = aggs['_max']
        try:
            delta = max_value - min_value
        except Exception:
            # cannot be subtracted or null values
            return None

        try:
            seconds = delta.total_seconds()
        except Exception:
            return None
        else:
            limit = 60 * 2
            if seconds < limit:
                # 119 seconds or less
                return 'second'
            limit *= 60
            if seconds < limit:
                # 2 minutes - 120 minutes
                return 'minute'
            limit *= 24
            if seconds < limit:
                # 2 hours - 48 hours
                return 'hour'
            limit *= 7
            if seconds < limit:
                # 2 days - 14 days
                return 'day'
            limit *= 4
            if seconds < limit:
                # 2 weeks - 8 weeks
                return 'week'
            limit *= 6
            if seconds < limit:
                # 2 months - 12 months
                return 'month'
            limit *= 4
            if seconds < limit:
                # 1 year - 4 years
                return 'quarter'
            # 4+ years
            return 'year'


    def _parse_combine_expression(self, expression, serializer=None, queryset=None, cast=None):
        serializer = serializer or self.get_serializer()
        if not expression:
            raise exceptions.ValidationError(
                "No value provided for combine query parameter"
            )
        if isinstance(expression, str):
            # strip whitespace
            expression = expression.strip()
            if ',' in expression:
                # a, b
                expression = [x.strip() for x in expression.split(',')]

        if isinstance(expression, list):
            result = [self._parse_combine_expression(x) for x in expression]
            return result if len(expression) > 1 else result[0]

        key = expression

        if ' as ' in expression.lower():
            try:
                expression, key = re.split(' as ', expression, flags=re.IGNORECASE)
            except ValueError:
                raise exceptions.ValidationError(f"Invalid expression: '{expression}'")

        arithmetic = re.search(REGEX.arithmetic_operator, expression)
        if arithmetic:
            arithmetic = arithmetic.group(0)
            splits = re.split(REGEX.arithmetic_operator_capture, expression)
            variables = []
            operators = []
            if len(splits) < 3:
                raise exceptions.ValidationError(f"Arithmetic exception: invalid expression: '{expression}'")
            for i, split in enumerate(splits):
                split = split.strip()
                if i % 2 == 0:
                    if split in self.ARITHMETIC_FUNCTIONS:
                        raise exceptions.ValidationError(f"Arithmetic exception: expecting a variable at position {i}, saw: '{split}'")
                    cast = None
                    if (i < len(splits) - 1 and splits[i+1] == '/') or (i > 0 and splits[i-1] == '/'):
                        # treat as float
                        cast = models.FloatField()
                    variables.append(self._parse_combine_expression(split, serializer=serializer, cast=cast))
                else:
                    if split not in self.ARITHMETIC_FUNCTIONS:
                        raise exceptions.ValidationError(f"Arithmetic exception: Expecting an operator at position {i}, saw: '{split}'")
                    operators.append(split)

            combined = None
            for i, operator in enumerate(operators):
                # TODO: handle MDAS order
                # for now, the order is left-to-right :)
                lhs = variables[i]['value'] if combined is None else combined
                rhs = variables[i+1]['value']
                fn = self.ARITHMETIC_FUNCTIONS[operator]
                combined = fn(lhs, rhs)

            result = {'key': key, 'value': combined, 'expression': expression}
            return result

        operator = value = None
        match = re.match(REGEX.function_expression, expression, flags=re.IGNORECASE)
        if match:
            # sum(b.c)
            operator = match.group(1).lower()
            value = match.group(2).lower()
            if match.group(3):
                # sum(b.c) as x
                key = match.group(3)
        else:
            match = re.match(REGEX.identifier_expression, expression, flags=re.IGNORECASE)
            if match:
                # b.c as x
                value = match.group(1)
                key = match.group(2)
            else:
                # b.c
                value = expression

        model_field = target = None
        resolved_model_fields = None
        if re.match(REGEX.identifier, value):
            try:
                model_fields, _ = serializer.resolve(value)
            except Exception:
                target = value
            else:
                resolved_model_fields = model_fields
                target = model_field = '__'.join([
                    Meta.get_query_name(f) for f in model_fields
                ])
        else:
            target = value

        # Named constant: identifier that did not resolve to a field → literal via get_named_constant
        if not operator and model_field is None and target is not None:
            constant = self.get_named_constant(str(target))
            if constant is not None:
                return {'key': key, 'value': constant, 'expression': expression}

        options = {}
        args = []
        fn = fn_cast = None
        if not operator:
            if model_field:
                fn = F
                # F(field)
            else:
                # literal value
                fn = lambda x, *_, **__: literalize(x)
        else:
            if operator == 'auto':
                # automatic buckets (date/time only)
                fn = self._compute_bucket_function(model_field, queryset=queryset) or 'month'
                fn = self.COMBINE_FUNCTIONS.get(fn, None)
            else:
                fn = self.COMBINE_FUNCTIONS.get(operator, None)

            if not fn:
                match = re.match(REGEX.word_number, operator)
                if match:
                    word = match.group(1)
                    number = int(match.group(2))
                    if word in self.COMBINE_FUNCTIONS:
                        if not isinstance(self.COMBINE_FUNCTIONS[word], dict) or 'python' not in self.COMBINE_FUNCTIONS[word]:
                            raise exceptions.ValidationError(
                                f'Cannot post-aggregate using {operator}'
                            )
                        # sum0/sum1 = sum given field by dimension 0 / 1
                        return {'value': None, 'key': key, 'then': [word, number, target], 'expression': expression}

                raise exceptions.ValidationError(
                    f'Unknown function: "{operator}"'
                )

        if isinstance(fn, dict):
            options = fn.get('options', options)
            args = fn.get('args', args)
            fn_cast = fn.get('cast')
            fn = fn['function']

        # For boolean fields, count/distinct should only count True values
        if (
            fn is Count
            and resolved_model_fields
            and isinstance(resolved_model_fields[-1], models.BooleanField)
        ):
            options['filter'] = Q(**{target: True})

        value = fn(target, *args, **options)
        if cast:
            value = Cast(value, cast)
        elif fn_cast:
            value = Cast(value, cast)
        return {'key': key, 'value': value, 'expression': expression}

    ARITHMETIC_FUNCTIONS = {
        '/': op.truediv,
        '*': op.mul,
        '+': op.add,
        '-': op.sub
    }
    COMBINE_FUNCTIONS = {
        'sum': {
            'function': Sum,
            'python': sum
        },
        'min': {
            'function': Min,
            'python': min
        },
        'max': {
            'function': Max,
            'python': max
        },
        'avg': {
            'function': Avg,
            'python': statistics.mean
        },
        'count': {
            'function': Count,
            'python': len
        },
        'distinct': {
            'function': Count,
            'python': lambda data: len(set(data)),
            'options': {
                'distinct': True
            }
        },
        'percent': {
            'python': percent
        },
        'year': {
            'function': Trunc,
            'cast': models.DateField(),
            'options': {'kind': 'year'}
        },
        'quarter': {
            'function': Trunc,
            'cast': models.DateField(),
            'options': {'kind': 'quarter'}
        },
        'month': {
            'function': Trunc,
            'cast': models.DateField(),
            'options': {'kind': 'month'}
        },
        'week': {
            'function': Trunc,
            'cast': models.DateField(),
            'options': {'kind': 'week'}
        },
        'day': {
            'function': Trunc,
            'cast': models.DateField(),
            'options': {'kind': 'day'}
        },
        'date': {
            'function': Trunc,
            'cast': models.DateField(),
            'options': {'kind': 'day'}
        },
        'hour': {
            'function': Trunc,
            'args': ['hour']
        },
        'minute': {
            'function': Trunc,
            'args': ['minute']
        },
        'second': {
            'function': Trunc,
            'args': ['second']
        },
        'length': Length,
        'lower': Lower,
        # 'reverse': Reverse,
        # 'md5': MD5,
        # 'sha256': SHA256,
        # 'sha512': SHA512,
        # 'trim': Trim,
        'upper': Upper
    }

    def get_named_constant(self, name):
        """
        Resolve a named constant used in combine expressions to a literal value.

        When an identifier in a combine expression (e.g. in arithmetic like
        count(field) / 10) is not a model field,
        this method is called with that identifier. Return a numeric (or other
        literal) value to use it in the expression; return None to fall back
        to the default behavior (literalize the string, which will keep the
        raw name and usually fail in aggregation).

        Override in a subclass to support named constants, e.g. by calling
        a class method: return getattr(self, name)() if callable(getattr(
        self, name, None)) else None.
        """
        return None

    def combine(self, request, combine, **kwargs):
        serializer = self.get_serializer()
        expression = combine.get('', None)
        by = combine.get('by', None)
        over = combine.get('over', None)
        flat = 'flat' in combine.get('format', [])
        base_queryset = self.filter_queryset(self.get_queryset())
        if has_joins(base_queryset):
            # if the base queryset has joins, we may produce inaccurate results if we aggregate
            # within the same queryset (if the joins can yield multiple output rows for each row
            # from the base table) -- instead, we replace the base queryset using a subquery approach
            queryset = base_queryset.model.objects.filter(pk__in=base_queryset.only('pk'))
        else:
            queryset = base_queryset

        expression = self._parse_combine_expression(expression, serializer, queryset)
        aggregations = {}
        thens = []
        if not isinstance(expression, list):
            expression = [expression]

        by_exs = []
        over_paths = []
        over_exs = []
        if by:
            by_exs = self._parse_combine_expression(by, serializer, queryset=queryset)
            if not isinstance(by_exs, list):
                by_exs = [by_exs]
            for ex in by_exs:
                if not ex['value']:
                    raise exceptions.ValidationError(f'Expression invalid for "by": {ex["expression"]}')
        if over:
            over_exs = self._parse_combine_expression(over, serializer, queryset=queryset)
            if not isinstance(over_exs, list):
                over_exs = [over_exs]
            for ex in over_exs:
                if not ex['value']:
                    raise exceptions.ValidationError(f'Expression invalid for "over": {ex["expression"]}')
                over_paths.append(ex['value'])

        for ex in expression:
            value = ex.get('value')
            then = ex.get('then')
            if value is not None:
                aggregations['_' + ex['key']] = value
            if then is not None:
                thens.append((ex['key'], *then))

        flat_data = []
        data = [] if flat else {}
        simple = True
        if by or over:
            data = {}
            values = []
            annotations = {}
            for ex in by_exs:
                by_key = '_' + ex['key']
                values.append(by_key)
                annotations[by_key] = ex['value']
            for ex in over_exs:
                over_key = '_' + ex['key']
                values.append(over_key)
                annotations[over_key] = ex['value']

            queryset = (
                queryset
                .annotate(**annotations)
                .values(*values)
                .annotate(**aggregations)
            )
            if over:
                queryset = queryset.order_by(*over_paths)
            else:
                # by only without over -> remove default ordering
                # this improves performance and prevents a grouping bug
                queryset = queryset.order_by()

            flat_data = remove_underscores(list(queryset))
            simple = False
        else:
            # simple aggregation (without "over" or "by")
            flat_data = remove_underscores([queryset.aggregate(**aggregations)])
            data = flat_data[0]

        if not simple and thens:
            dimensions = [x['key'] for x in by_exs + over_exs]
            for then in thens:
                # post-aggregates
                key, function, dimension, ref = then
                cache_level = 'values' if function == 'percent' else 'data'
                if dimension > len(dimensions):
                    dimension = len(dimensions)
                fn = self.COMBINE_FUNCTIONS[function]['python']
                cache = {}

                def get_cache_key(row):
                    if dimension == 0:
                        return '1'
                    return tuple(row.get(dimensions[i]) for i in range(dimension))

                def is_grouped(row, other):
                    return row == other or get_cache_key(row) == get_cache_key(other)

                def get_values(row, cache_key):
                    if cache_level == 'values' and cache_key in cache:
                        return cache[cache_key]

                    base = [x.get(ref) for x in flat_data if is_grouped(row, x)]
                    # None usually throws off statistic functions
                    result = [x for x in base if x is not None]
                    if cache_level == 'values':
                        cache[cache_key] = result
                    return result

                def get_data(row):
                    cache_key = get_cache_key(row)
                    if cache_level == 'data' and cache_key in cache:
                        return cache[cache_key]

                    vals = get_values(row, cache_key)
                    if cache_level == 'data':
                        result = fn(vals)
                    else:
                        result = fn(vals, this=row.get(ref))
                    if cache_level == 'data':
                        cache[cache_key] = result
                    return result

                for row in flat_data:
                    row[key] = get_data(row)

        if flat:
            data = flat_data
        elif not simple:
            # return a nested view on the data:
            #
            # simple, with no by/over:
            #
            # ex0: value
            # ex1: value
            # ...
            #
            # with 2 bys and 2 overs:
            #
            # by0:
            #    by1:
            #       ex0:
            #           [over0_0, over0_1, value0]
            #       ex1:
            #           [over1_0, over1_1, value1]
            #

            data = {}
            x = None
            bys = None
            for item in flat_data:
                bys = []
                x = []
                for ex in by_exs:
                    bys.append(item.get(ex['key']))
                for ex in over_exs:
                    x.append(item.get(ex['key']))

                for ex in expression:
                    key = ex['key']
                    y = item[key]
                    if by:
                        d = data
                        for b in bys:
                            if b not in d:
                                d[b] = {}
                            d = d[b]
                        if over:
                            # over and by
                            if key not in d:
                                d[key] = []
                            d[key].append(
                                [*x, y]
                            )
                        else:
                            # by without over
                            d[key] = y
                    else:
                        # over without by
                        if key not in data:
                            data[key] = []
                        data[key].append(
                           [*x, y]
                        )
        response = {'data': clean(data)}
        debug = self.get_request_debug()
        if debug:
            response['meta'] = {'query': str(queryset.query)}
        return Response(response, status=200)

    def list_related(self, request, pk=None, field_name=None):
        """Fetch related object(s) through a relation field.

        For many-relations, routes through a dynamically-created viewset
        to get full support for filtering, sorting, pagination (default 50),
        and include[]/exclude[].

        For single relations (FK, O2O), returns the object directly.

        Permissions are checked on the parent object: if you can read
        the parent and the field is accessible, you can list the related
        objects.

        This method gets mapped to `/<resource>/<pk>/<field_name>/` by
        DynamicRouter for all DynamicRelationField fields.
        """

        # Get the field from a temporary serializer (for validation only)
        serializer_class = self.get_serializer_class()
        temp_serializer = serializer_class()
        all_fields = temp_serializer.get_all_fields()
        field = all_fields.get(field_name)

        if field is None:
            raise exceptions.NotFound('Unknown field: "%s".' % field_name)

        if not hasattr(field, 'serializer_class'):
            raise exceptions.NotFound(
                '"%s" is not a related field.' % field_name
            )

        is_many = field.many

        # Check parent exists and user has read permission.
        # get_queryset() applies permission-based filtering, so if
        # the user cannot read the parent, .get() raises NotFound.
        parent_qs = self.get_queryset()
        try:
            instance = parent_qs.get(pk=pk)
        except parent_qs.model.DoesNotExist:
            raise exceptions.NotFound()

        # Check field-level permissions from the parent serializer.
        permissions = getattr(self, 'full_permissions', None)
        if permissions:
            field_perms = permissions.fields
            if (
                field_perms
                and not field_perms.no_access
                and field_name in field_perms.spec
            ):
                field_spec = field_perms.spec[field_name]
                if isinstance(field_spec, dict) and (
                    field_spec.get('read') is False
                ):
                    raise exceptions.PermissionDenied()

        if not is_many:
            # Single relation (FK / O2O): use a fully-bound serializer
            # to properly resolve and render the related object.
            self._prefix_inex_params(request, self.INCLUDE, field_name + '.')
            self._prefix_inex_params(request, self.EXCLUDE, field_name + '.')
            self.request.query_params.add('filter{pk}', pk)
            self.request.query_params.add(self.INCLUDE, field_name + '.')
            self._refresh_query_params()

            serializer = self.get_serializer()
            bound_field = serializer.fields.get(field_name)
            if bound_field is None:
                bound_field = serializer.get_all_fields().get(field_name)

            queryset = self.get_queryset()
            queryset = self.filter_queryset(queryset)
            parent = queryset.first()
            if not parent:
                raise exceptions.NotFound()

            related = bound_field.get_related(parent)
            if not related:
                return Response({}, status=200)

            related_serializer = bound_field.get_serializer(
                instance=related, envelope=True
            )
            return Response(related_serializer.data)

        # Many relation: route through a dynamic viewset so we get
        # filtering, sorting, pagination, and includes for free.
        source = field.source or field_name
        related_obj = getattr(instance, source)

        # If the field declares a custom queryset, honor its filtering
        # and ordering by starting from it and restricting to objects
        # reachable through the parent relation.
        field_queryset = field.queryset
        if callable(field_queryset):
            field_queryset = field_queryset(temp_serializer)

        if field_queryset is not None and hasattr(related_obj, 'all'):
            related_qs = field_queryset.filter(
                pk__in=related_obj.all().values('pk')
            )
        elif hasattr(related_obj, 'all'):
            related_qs = related_obj.all()
        else:
            related_qs = related_obj

        related_serializer_class = field.serializer_class

        # Build a one-off viewset for the related model.
        # Inherits dynamic features (but NOT PermissionsViewSetMixin,
        # because access is already gated by the parent check above).
        _related_qs = related_qs
        _features = WithDynamicViewSetBase.features

        class _RelatedListViewSet(
            WithDynamicViewSetBase, ListModelMixin, viewsets.GenericViewSet
        ):
            serializer_class = related_serializer_class
            features = _features
            pagination_class = type(
                '_RelatedPagination',
                (DynamicPageNumberPagination,),
                {'page_size': 50},
            )

            def get_queryset(inner_self, queryset=None):
                return _related_qs

        viewset = _RelatedListViewSet()
        viewset.request = request
        viewset.args = ()
        viewset.kwargs = {}
        viewset.format_kwarg = self.format_kwarg
        return viewset.list(request)


class WithDynamicViewSetMixin(PermissionsViewSetMixin, WithDynamicViewSetBase):
    pass


class DynamicModelViewSet(WithDynamicViewSetMixin, viewsets.ModelViewSet):

    ENABLE_BULK_PARTIAL_CREATION = settings.ENABLE_BULK_PARTIAL_CREATION
    ENABLE_BULK_UPDATE = settings.ENABLE_BULK_UPDATE

    def _get_bulk_payload(self, request):
        if self._is_csv_upload(request):
            return self._get_bulk_payload_csv(request)
        else:
            return self._get_bulk_payload_json(request)

    def _is_csv_upload(self, request):
        if is_form_media_type(request.content_type):
            if 'file' in request.data and request.data['file'].name.lower().endswith(
                '.csv'
            ):
                return True
        return False

    def _get_bulk_payload_csv(self, request):
        file = request.data['file']
        reader = csv.DictReader(StringIO(file.read().decode('utf-8')))
        return [r for r in reader]

    def _get_bulk_payload_json(self, request):
        plural_name = self.get_serializer_class().get_plural_name()
        if isinstance(request.data, list):
            return request.data
        elif plural_name in request.data and len(request.data) == 1:
            return request.data[plural_name]
        return None

    def _bulk_update(self, data, partial=False):
        # Restrict the update to the filtered queryset.
        serializer = self.get_serializer(
            self.filter_queryset(self.get_queryset()),
            data=data,
            many=True,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """Either update  a single or many model instances. Use list to
        indicate bulk update.

        Examples:

        PATCH /dogs/1/
        {
            'fur': 'white'
        }

        PATCH /dogs/
        {
            'dogs': [
                {'id': 1, 'fur': 'white'},
                {'id': 2, 'fur': 'black'},
                {'id': 3, 'fur': 'yellow'}
            ]
        }

        PATCH /dogs/?filter{fur.contains}=brown
        [
            {'id': 3, 'fur': 'gold'}
        ]
        """
        if self.ENABLE_BULK_UPDATE:
            partial = 'partial' in kwargs
            bulk_payload = self._get_bulk_payload(request)
            if bulk_payload:
                return self._bulk_update(bulk_payload, partial)
        return super(DynamicModelViewSet, self).update(request, *args, **kwargs)

    def _create_many(self, data):
        items = []
        errors = []
        result = {}
        serializers = []

        for entry in data:
            serializer = self.get_serializer(data=entry)
            try:
                serializer.is_valid(raise_exception=True)
            except exceptions.ValidationError as e:
                errors.append({'detail': e.detail, 'source': entry})
            else:
                if self.ENABLE_BULK_PARTIAL_CREATION:
                    self.perform_create(serializer)
                    items.append(serializer.to_representation(serializer.instance))
                else:
                    serializers.append(serializer)
        if not self.ENABLE_BULK_PARTIAL_CREATION and not errors:
            for serializer in serializers:
                self.perform_create(serializer)
                items.append(serializer.to_representation(serializer.instance))

        # Populate serialized data to the result.
        result = SideloadingProcessor(self.get_serializer(), items).data

        # Include errors if any.
        if errors:
            result['errors'] = errors

        code = status.HTTP_201_CREATED if not errors else status.HTTP_400_BAD_REQUEST

        return Response(result, status=code)

    def create(self, request, *args, **kwargs):
        """
        Either create a single or many model instances in bulk
        using the Serializer's many=True ability from Django REST >= 2.2.5.

        The data can be represented by the serializer name (single or plural
        forms), dict or list.

        Examples:

        POST /dogs/
        {
          "name": "Fido",
          "age": 2
        }

        POST /dogs/
        {
          "dog": {
            "name": "Lucky",
            "age": 3
          }
        }

        POST /dogs/
        {
          "dogs": [
            {"name": "Fido", "age": 2},
            {"name": "Lucky", "age": 3}
          ]
        }

        POST /dogs/
        [
            {"name": "Fido", "age": 2},
            {"name": "Lucky", "age": 3}
        ]
        """
        bulk_payload = self._get_bulk_payload(request)
        if bulk_payload:
            return self._create_many(bulk_payload)
        response = super(DynamicModelViewSet, self).create(request, *args, **kwargs)
        serializer = getattr(response.data, 'serializer')
        if serializer and serializer.instance:
            url = serializer.get_url(pk=serializer.instance.pk)
            response['Location'] = url
        return response

    def _destroy_many(self, data):
        instances = (
            self.get_queryset().filter(id__in=[d['id'] for d in data]).distinct()
        )
        for instance in instances:
            self.check_object_permissions(self.request, instance)
            self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, *args, **kwargs):
        """
        Either delete a single or many model instances in bulk

        DELETE /dogs/
        {
            "dogs": [
                {"id": 1},
                {"id": 2}
            ]
        }

        DELETE /dogs/
        [
            {"id": 1},
            {"id": 2}
        ]
        """
        bulk_payload = self._get_bulk_payload(request)
        if bulk_payload:
            return self._destroy_many(bulk_payload)
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        if lookup_url_kwarg not in kwargs:
            # assume that it is a poorly formatted bulk request
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        return super(DynamicModelViewSet, self).destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        if self.SET_REQUEST_ON_SAVE:
            attr = (
                self.SET_REQUEST_ON_SAVE
                if isinstance(self.SET_REQUEST_ON_SAVE, str)
                else '_request'
            )
            setattr(instance, attr, self.request)
        instance.delete()
