"""This module contains custom serializer classes."""

import copy
import inspect

from collections import OrderedDict
import inflection
from django.db import models, transaction
from django.db.models.fields.files import FieldFile
from django.utils.functional import cached_property
from rest_framework import exceptions, serializers
from rest_framework.fields import SkipField, empty
from rest_framework.reverse import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.utils.serializer_helpers import ReturnDict, ReturnList

from dynamic_rest.ui import UIFilter, UISection, UIField
from dynamic_rest.permissions import PermissionsSerializerMixin
from dynamic_rest.conf import settings
from dynamic_rest import fields as _fields
from dynamic_rest.links import merge_link_object
from dynamic_rest.meta import Meta, get_model_table, get_model_field, get_related_model
from dynamic_rest.processors import SideloadingProcessor
from dynamic_rest.tagged import tag_dict
from dynamic_rest.base import DynamicBase


def nested_update(instance, key, value, objects=None):
    objects = objects or []
    nested = getattr(instance, key, None)

    def fix(x):
        s = str(x).lower()
        if s == "true":
            return "True"
        if s == "false":
            return "False"
        return x

    value = {k: fix(v) for k, v in value.items()}
    if not nested:
        # object does not exist, try to create it
        try:
            field = get_model_field(instance, key)
            related_model = get_related_model(field)
        except AttributeError:
            raise exceptions.ValidationError("Invalid relationship: %s" % key)
        else:
            nested = related_model.objects.create(**value)
            setattr(instance, key, nested)
    else:
        # object exists, perform a nested update
        for k, v in value.items():
            if isinstance(v, dict):
                nested_update(nested, k, v, objects)
            else:
                if isinstance(getattr(nested, k), models.Manager):
                    getattr(nested, k).set(v)
                else:
                    setattr(nested, k, v)
        objects.append(nested)
    return objects


class WithResourceKeyMixin(object):
    @classmethod
    def get_resource_key(self):
        """Return canonical resource key, usually the DB table name."""
        model = self.get_model()
        if model:
            return get_model_table(model)
        else:
            return self.get_name()


class DynamicListSerializer(WithResourceKeyMixin, serializers.ListSerializer):
    """Custom ListSerializer class.

    This implementation delegates DREST-specific methods to
    the child serializer and performs post-processing before
    returning the data.
    """

    update_lookup_field = "id"

    def __init__(self, *args, **kwargs):
        super(DynamicListSerializer, self).__init__(*args, **kwargs)
        self.child.parent = self

    @property
    def create_related_serializers(self):
        return None

    def get_field_permissions(self):
        return self.child.get_field_permissions()

    def get_router(self):
        return self.child.get_router()

    def set_request_method(self, method):
        return self.child.set_request_method(method)

    def get_all_fields(self):
        return self.child.get_all_fields()

    def get_link_fields(self):
        return self.child.get_link_fields()

    def get_id_fields(self):
        return self.child.get_id_fields()

    def __iter__(self):
        return self.child.__iter__()

    def get_field(self, name, **kwargs):
        return self.child.get_field(name, **kwargs)

    @property
    def fields(self):
        return self.child.fields

    def get_filters(self):
        return self.child.get_filters()

    def get_permissions(self):
        return self.child.get_permissions()

    def get_meta(self):
        return self.child.get_meta()

    def get_section(self):
        return self.child.get_section()

    def get_sections(self):
        return self.child.get_sections()

    def disable_envelope(self):
        self.child.disable_envelope()
        self._processed_data = None

    def to_representation(self, data):
        iterable = data.all() if isinstance(data, models.Manager) else data
        return [self.child.to_representation(item) for item in iterable]

    def get_description(self):
        return self.child.get_description()

    def resolve(self, query, **kwargs):
        return self.child.resolve(query, **kwargs)

    def get_name_field(self):
        return self.child.get_name_field()

    def get_image_field(self):
        return self.child.get_image_field()

    def get_class_getter(self):
        return self.child.get_class_getter()

    def get_search_key(self):
        return self.child.get_search_key()

    def get_icon(self):
        return self.child.get_icon()

    def initialized(self, **kwargs):
        return self.child.initialized(**kwargs)

    def get_style(self):
        return self.child.get_style()

    def get_url(self, pk=None):
        return self.child.get_url(pk=pk)

    def get_model(self):
        return self.child.get_model()

    def get_pk_field(self):
        return self.child.get_pk_field()

    def get_format(self):
        return self.child.get_format()

    def get_name(self):
        return self.child.get_name()

    def get_plural_name(self):
        return self.child.get_plural_name()

    def id_only(self):
        return self.child.id_only()

    @property
    def data(self):
        """Get the data, after performing post-processing if necessary."""
        if getattr(self, "_processed_data", None) is None:
            data = super(DynamicListSerializer, self).data
            self._processed_data = (
                ReturnDict(SideloadingProcessor(self, data).data, serializer=self)
                if self.child.envelope
                else ReturnList(data, serializer=self)
            )
        return self._processed_data

    def update(self, queryset, validated_data):
        lookup_attr = getattr(self.child.Meta, "update_lookup_field", "id")

        lookup_objects = {entry.pop(lookup_attr): entry for entry in validated_data}

        lookup_keys = lookup_objects.keys()

        if not all((bool(_) and not inspect.isclass(_) for _ in lookup_keys)):
            raise exceptions.ValidationError("Invalid lookup key value.")

        # Since this method is given a queryset which can have many
        # model instances, first find all objects to update
        # and only then update the models.
        objects_to_update = queryset.filter(
            **{"{}__in".format(lookup_attr): lookup_keys}
        )

        if len(lookup_keys) != objects_to_update.count():
            raise exceptions.ValidationError(
                "Could not find all objects to update: {} != {}.".format(
                    len(lookup_keys), objects_to_update.count()
                )
            )

        updated_objects = []
        for object_to_update in objects_to_update:
            lookup_key = getattr(object_to_update, lookup_attr)
            data = lookup_objects.get(lookup_key)
            # Use model serializer to actually update the model
            # in case that method is overwritten.
            updated_objects.append(self.child.update(object_to_update, data))

        return updated_objects


class WithDynamicSerializerMixin(
    DynamicBase,
    WithResourceKeyMixin,
):
    """Base class for DREST serializers.

    This class provides support for dynamic field inclusions/exclusions.

    Like DRF, DREST serializers support a few Meta class options:
        - model - class
        - name - string
        - plural_name - string
        - defer_many_relations - bool
        - fields - list of strings
        - deferred_fields - list of strings
        - immutable_fields - list of strings
        - read_only_fields - list of strings
        - untrimmed_fields - list of strings
        - depends - dict of dependency objects
    """

    _ALL_FIELDS_CACHE = {}
    SET_REQUEST_ON_SAVE = settings.SET_REQUEST_ON_SAVE

    def __new__(cls, *args, **kwargs):
        """
        Custom constructor that sets the ListSerializer to
        DynamicListSerializer to avoid re-evaluating querysets.

        Addresses DRF 3.1.0 bug:
        https://github.com/tomchristie/django-rest-framework/issues/2704
        """
        meta = getattr(cls, "Meta", None)
        if not meta:
            meta = type("Meta", (), {})
            cls.Meta = meta
        list_serializer_class = getattr(
            meta, "list_serializer_class", DynamicListSerializer
        )
        if not issubclass(list_serializer_class, DynamicListSerializer):
            list_serializer_class = DynamicListSerializer
        meta.list_serializer_class = list_serializer_class
        return super(WithDynamicSerializerMixin, cls).__new__(cls, *args, **kwargs)

    def __init__(
        self,
        instance=None,
        data=empty,
        only_fields=None,
        include_fields=None,
        exclude_fields=None,
        request_fields=None,
        sideloading=None,
        debug=False,
        dynamic=True,
        embed=False,
        envelope=False,
        request_method=None,
        for_metadata=False,
        **kwargs,
    ):
        """
        Custom initializer that builds `request_fields`.

        Arguments:
            instance: Initial instance, used by updates.
            data: Initial data, used by updates / creates.
            only_fields: List of field names to render.
            include_fields: List of field names to include.
            exclude_fields: List of field names to exclude.
            request_fields: Map of field names that supports
                nested inclusions / exclusions.
            embed: If True, embed the current representation.
                If False, sideload the current representation.
            sideloading: If True, force sideloading for all descendents.
                If False, force embedding for all descendents.
                If None (default), respect descendents' embed parameters.
            dynamic: If False, disable inclusion / exclusion features.
            envelope: If True, wrap `.data` in an envelope.
                If False, do not use an envelope.
        """
        if request_method:
            self.set_request_method(request_method)

        name = self.get_name()
        if data is not empty and name in data and len(data) == 1:
            # support POST/PUT key'd by resource name
            data = data[name]
        if data is not empty:
            # if a field is nullable but not required and the implementation
            # passes null as a value, remove the field from the data
            # this addresses the frontends that send
            # undefined resource fields as null on POST/PUT
            for field_name, field in self.get_all_fields().items():
                if 'allow_null' in getattr(field, 'kwargs', {}):
                    allow_null = field.allow_null
                else:
                    # infer from model field
                    model_field = getattr(field, 'model_field', None)
                    model_allows_null = getattr(model_field, 'null', False) if model_field else False
                    allow_null = field.allow_null or model_allows_null
                if (
                    allow_null is False
                    and field.required is False
                    and field_name in data
                    and data[field_name] is None
                ):
                    data.pop(field_name)

        kwargs["instance"] = instance
        kwargs["data"] = data

        # "sideload" argument is pending deprecation
        if kwargs.pop("sideload", False):
            # if "sideload=True" is passed, turn on the envelope
            envelope = True

        self.parent = None
        self.for_metadata = for_metadata

        super(WithDynamicSerializerMixin, self).__init__(**kwargs)

        self.envelope = envelope
        self.sideloading = sideloading
        self.debug = debug
        self.dynamic = dynamic
        self.request_fields = request_fields or {}

        # `embed` is overriden by `sideloading`
        embed = embed if sideloading is None else not sideloading
        self.embed = embed

        self._dynamic_init(only_fields, include_fields, exclude_fields)
        self.enable_optimization = settings.ENABLE_SERIALIZER_OPTIMIZATIONS

    def __getitem__(self, key):
        field = self.fields[key]
        value = self.data.get(key)
        error = self.errors.get(key) if hasattr(self, "_errors") else None
        if not isinstance(field, serializers.Serializer):
            return UIField(field, value, error, instance=self.instance)
        else:
            return super(WithDynamicSerializerMixin, self).__getitem__(key)

    @cached_property
    def create_related_serializers(self):
        return self.get_create_related_serializers()

    def get_create_related_serializers(self, instance=None):
        instance = instance or self.instance
        forms = {}
        if instance:
            for related_name, field in self.get_link_fields().items():
                source = field.source or related_name
                has_source = source != "*"
                if not has_source:
                    continue
                kwargs = {
                    "request_fields": None,
                    "request_method": "POST",
                    "many": False,
                }
                inverse_field_name = field.get_inverse_field_name()
                if inverse_field_name:
                    kwargs["exclude_fields"] = [inverse_field_name]
                related_serializer = field.get_serializer(**kwargs)
                if inverse_field_name:
                    inverse = related_serializer.get_field(inverse_field_name)
                    inverse.read_only = True
                else:
                    pass
                has_permission = (
                    not getattr(related_serializer, "permissions", None)
                    or related_serializer.permissions.create
                )
                can_create = (
                    field.create
                    and (field.many or getattr(instance, source, None) is None)
                    and has_permission
                )

                if can_create:
                    if hasattr(related_serializer, "initialized"):
                        # call initialized for permissions hooks/etc
                        # this is simulating a primary request
                        # so do not pass nested=True
                        related_serializer.initialized()
                    forms[related_name] = related_serializer
        return forms

    def get_router(self):
        return getattr(self, "_router", None)

    def initialized(self, **kwargs):
        return

    def _dynamic_init(self, only_fields, include_fields, exclude_fields):
        """
        Modifies `request_fields` via higher-level dynamic field interfaces.

        Arguments:
            only_fields: List of field names to render.
                All other fields will be deferred (respects sideloads).
            include_fields: List of field names to include.
                Adds to default field set, (respects sideloads).
                `*` means include all fields.
            exclude_fields: List of field names to exclude.
                Removes from default field set. If set to '*', all fields are
                removed, except for ones that are explicitly included.
        """

        if not self.dynamic:
            return

        if (
            isinstance(self.request_fields, dict)
            and self.request_fields.pop("*", None) is False
        ):
            exclude_fields = "*"

        only_fields = set(only_fields or [])
        include_fields = include_fields or []
        exclude_fields = exclude_fields or []
        all_fields = set(self.get_all_fields().keys())

        if only_fields:
            exclude_fields = "*"
            include_fields = only_fields

        if exclude_fields == "*":
            # First exclude all, then add back in explicitly included fields.
            include_fields = set(
                list(include_fields)
                + [
                    field
                    for field, val in self.request_fields.items()
                    if val or val == {}
                ]
            )
            exclude_fields = all_fields - include_fields
        elif include_fields == "*":
            include_fields = all_fields

        for name in exclude_fields:
            self.request_fields[name] = False

        for name in include_fields:
            if not isinstance(self.request_fields.get(name), dict):
                # not sideloading this field
                self.request_fields[name] = True

    @cached_property
    def default_sections(self):
        field_names = self._all_readable_field_names
        instance = self.instance
        return [UISection("Details", field_names, self, instance, main=True)]

    def get_section(self):
        return getattr(self.get_meta(), "section", None)

    def get_sections(self, instance=None):
        sections = getattr(self.get_meta(), "sections", {})

        if not sections:
            return self.default_sections

        if isinstance(sections, dict):
            sections = sections.items()

        return [
            UISection(name, value, self, instance=instance) for name, value in sections
        ]

    def get_filters(self):
        filters = getattr(self.get_meta(), "filters", {})
        if isinstance(filters, dict):
            filters = filters.items()
        return OrderedDict(
            ((name, UIFilter(name, value, serializer=self)) for name, value in filters)
        )

    def get_field_permissions(self):
        permissions = {}
        for name, field in self.fields.items():
            permissions[name] = {}
            permissions[name]["read"] = not field.write_only
            if field.read_only:
                permissions[name]["write"] = False
            elif not field.immutable and not field.only_update:
                permissions[name]["write"] = True
            else:
                permissions[name]["write"] = {
                    "update": not field.immutable,
                    "create": not field.only_update,
                }
            if hasattr(field, "create"):
                permissions[name]["create"] = field.create
        return permissions

    def get_field_value(self, key, instance=None):
        if instance == "":
            instance = None

        field = self.fields[key]
        if hasattr(field, "prepare_value"):
            value = field.prepare_value(instance)
        else:
            attr = field.get_attribute(instance)
            value = field.to_representation(attr) if attr else None

        if not isinstance(value, FieldFile):
            if isinstance(value, list):
                value = [getattr(v, "instance", v) for v in value]
            else:
                value = getattr(value, "instance", value)
        error = self.errors.get(key) if hasattr(self, "_errors") else None
        return UIField(field, value, error, prefix="", instance=instance)

    def get_pk_field(self):
        try:
            field = self.get_field("pk")
            return field.field_name
        except AttributeError:
            pass
        return "pk"

    @classmethod
    def get_style(cls):
        meta = cls.get_meta()
        return getattr(meta, "style", {})

    @classmethod
    def get_icon(cls):
        meta = cls.get_meta()
        return getattr(meta, "icon", None)

    @classmethod
    def get_meta(cls):
        return cls.Meta

    def resolve(self, query, sort=None):
        """Resolves a query into model and serializer fields.

        Arguments:
            query: an API field path, in dot-nation
                e.g: "creator.location_name"

        Returns:
            (model_fields, api_fields)
                e.g:
                    [
                        Blog._meta.fields.user,
                        User._meta.fields.location,
                        Location._meta.fields.name
                    ],
                    [
                        DynamicRelationField(source="user"),
                        DynamicCharField(source="location.name")
                    ]

        Raises:
            ValidationError if the query is invalid,
                e.g. references a method field or an undefined field
        ```

        Note that the lists do not necessarily contain the
        same number of elements because API fields can reference nested model fields.
        """  # noqa
        if not isinstance(query, str):
            parts = query
            query = ".".join(query)
        else:
            parts = query.split(".")

        model_fields = []
        api_fields = []

        serializer = self

        model = serializer.get_model()
        resource_name = serializer.get_name()
        meta = Meta(model)
        api_name = parts[0]
        other = parts[1:]

        try:
            api_field = serializer.get_field(api_name)
            if isinstance(api_field, _fields.DynamicRelationField):
                api_field.bind(parent=self, field_name=api_name)
        except AttributeError:
            api_field = None

        if other:
            # Check if this is a JSON field path
            is_json_field = False
            if api_field and hasattr(api_field, 'source'):
                source = api_field.source or api_name
                try:
                    model_field = meta.get_field(source)
                    # Check if it's a JSONField
                    from django.db.models import JSONField
                    if isinstance(model_field, JSONField):
                        is_json_field = True
                except AttributeError:
                    pass

            if not (api_field and isinstance(api_field, _fields.DynamicRelationField)) and not is_json_field:
                raise ValidationError(
                    {
                        api_name: 'Could not resolve "%s": '
                        '"%s.%s" is not an API relation'
                        % (query, resource_name, api_name)
                    }
                )

            if is_json_field:
                # For JSON fields, we don't need to resolve further
                # Django's ORM will handle the path directly
                source = api_field.source or api_name
                try:
                    model_field = meta.get_field(source)
                except AttributeError:
                    raise ValidationError(
                        {
                            api_name: 'Could not resolve "%s": '
                            '"%s.%s" is not a model field'
                            % (query, meta.get_name(), source)
                        }
                    )
                model_fields.append(model_field)
                api_fields.append(api_field)
            else:
                # Handle regular relations
                source = api_field.source or api_name
                related = api_field.serializer_class()
                other = ".".join(other)
                model_fields, api_fields = related.resolve(other, sort=sort)

                try:
                    model_field = meta.get_field(source)
                except AttributeError:
                    raise ValidationError(
                        {
                            api_name: 'Could not resolve "%s": '
                            '"%s.%s" is not a model relation'
                            % (query, meta.get_name(), source)
                        }
                    )

                model_fields.insert(0, model_field)
                api_fields.insert(0, api_field)
        else:
            if api_name == "pk":
                # pk is an alias for the id field
                model_field = meta.get_pk_field()
                model_fields.append(model_field)
                if api_field:
                    # the pk field may not exist
                    # on the serializer
                    api_fields.append(api_field)
            else:
                if not api_field:
                    raise ValidationError(
                        {
                            api_name: 'Could not resolve "%s": '
                            '"%s.%s" is not an API field'
                            % (query, resource_name, api_name)
                        }
                    )

                api_fields.append(api_field)

                source = api_field.source or api_name
                if sort and getattr(api_field, "sort_by", None):
                    # use sort_by source
                    source = api_field.sort_by

                if source == "*":
                    # a method field was requested and has no sort_by
                    # -> model field is unknown
                    return (model_fields, api_fields)

                if "." in source:
                    fields = source.split(".")
                    for field in fields[:-1]:
                        related_model = None
                        try:
                            model_field = meta.get_field(field)
                            related_model = model_field.related_model
                        except AttributeError:
                            pass

                        if not related_model:
                            raise ValidationError(
                                {
                                    api_name: 'Could not resolve "%s": '
                                    '"%s.%s" is not a model relation'
                                    % (query, meta.get_name(), field)
                                }
                            )
                        model = related_model
                        meta = Meta(model)
                        model_fields.append(model_field)
                    field = fields[-1]
                    try:
                        model_field = meta.get_field(field)
                    except AttributeError:
                        raise ValidationError(
                            {
                                api_name: 'Could not resolve: "%s", '
                                '"%s.%s" is not a model field'
                                % (query, meta.get_name(), field)
                            }
                        )
                    model_fields.append(model_field)
                else:
                    try:
                        model_field = meta.get_field(source)
                    except AttributeError:
                        raise ValidationError(
                            {
                                api_name: 'Could not resolve "%s": '
                                '"%s.%s" is not a model field'
                                % (query, meta.get_name(), source)
                            }
                        )
                    model_fields.append(model_field)

        return (model_fields, api_fields)

    def disable_envelope(self):
        envelope = self.envelope
        self.envelope = False
        if envelope:
            self._processed_data = None

    @classmethod
    def get_model(cls):
        """Get the model, if the serializer has one.

        Model serializers should implement this method.
        """
        return None

    def get_field(self, field_name):
        # it might be deferred
        fields = self.get_all_fields()
        if field_name == "pk":
            meta = self.get_meta()
            if hasattr(meta, "_pk"):
                return meta._pk

            field = None
            model = self.get_model()
            primary_key = getattr(meta, "primary_key", None)

            if primary_key:
                field = fields.get(primary_key)
            else:
                for n, f in fields.items():
                    # try to use model fields
                    try:
                        if getattr(field, "primary_key", False):
                            field = f
                            break

                        model_field = get_model_field(model, f.source or n)

                        if model_field.primary_key:
                            field = f
                            break
                    except AttributeError:
                        pass

            if not field:
                # fall back to a field called ID
                if "id" in fields:
                    field = fields["id"]

            if field:
                meta._pk = field
                return field
        else:
            if field_name in fields:
                field = fields[field_name]
                return field

        raise ValidationError({field_name: '"%s" is not an API field' % field_name})

    def get_format(self):
        view = self.context.get("view")
        get_format = getattr(view, "get_format", None)
        if callable(get_format):
            return get_format()
        return None

    @classmethod
    def get_name(cls):
        """Get the serializer name.

        The name can be defined on the Meta class or will be generated
        automatically from the model name.
        """
        if not hasattr(cls.Meta, "name"):
            class_name = getattr(cls.get_model(), "__name__", None)
            setattr(
                cls.Meta,
                "name",
                inflection.underscore(class_name) if class_name else None,
            )

        return cls.Meta.name

    @classmethod
    def get_url(self, pk=None):
        # if associated with a registered viewset, use its URL
        url = getattr(self, "_url", None)
        if url:
            # use URL key to get endpoint
            url = reverse(url)
        if not url:
            # otherwise, return canonical URL for this model
            from dynamic_rest.routers import DynamicRouter

            url = DynamicRouter.get_canonical_path(self.get_resource_key())

        if pk:
            return "%s/%s/" % (url, pk)

        if url and not url.endswith("/"):
            url = url + "/"
        return url

    @classmethod
    def get_description(cls):
        return getattr(cls.Meta, "description", None)

    @classmethod
    def get_class_getter(self):
        meta = self.get_meta()
        return getattr(meta, "get_classes", None)

    @classmethod
    def get_name_field(cls):
        if not hasattr(cls.Meta, "name_field"):
            # fallback to primary key
            return "pk"
        return cls.Meta.name_field

    @classmethod
    def get_image_field(cls):
        if not hasattr(cls.Meta, "image_field"):
            # fallback to primary key
            return None
        return cls.Meta.image_field

    @classmethod
    def get_search_key(cls):
        meta = cls.get_meta()
        if hasattr(meta, "search_key"):
            return meta.search_key

        # fallback to name field
        name_field = cls.get_name_field()
        if name_field:
            return "filter{%s.icontains}" % name_field

        # fallback to PK
        return "pk"

    @classmethod
    def get_plural_name(cls):
        """Get the serializer's plural name.

        The plural name may be defined on the Meta class.
        If the plural name is not defined,
        the pluralized form of the name will be returned.
        """
        if not hasattr(cls.Meta, "plural_name"):
            setattr(cls.Meta, "plural_name", inflection.pluralize(cls.get_name()))
        return cls.Meta.plural_name

    def get_request_attribute(self, attribute, default=None):
        return getattr(self.context.get("request"), attribute, default)

    def set_request_method(self, method=None):
        self._request_method = method

    def get_request_method(self):
        if getattr(self, "_request_method", None):
            return self._request_method
        else:
            return self.get_request_attribute("method", "").upper()

    @classmethod
    def _cached_get_all_fields(cls, serializer):
        name = serializer.__class__.__module__ + "." + serializer.__class__.__qualname__
        if name in cls._ALL_FIELDS_CACHE and settings.ENABLE_ALL_FIELDS_CACHE:
            return cls._ALL_FIELDS_CACHE[name]

        serializer._all_fields = fields = super(
            WithDynamicSerializerMixin, serializer
        ).get_fields()
        for k, field in serializer._all_fields.items():
            serializer.setup_field(k, field)

        cls._ALL_FIELDS_CACHE[name] = fields
        return fields

    def get_all_fields(self):
        """Returns the entire serializer field set.

        Does not respect dynamic field inclusions/exclusions.
        """
        if not hasattr(self, "_all_fields"):
            self._all_fields = self._cached_get_all_fields(self)

        return self._all_fields

    def setup_field(self, name, field):
        field.field_name = name
        field.parent = self
        label = inflection.humanize(name)
        field.label = getattr(field, "label", label) or label

        fields = {name: field}
        meta = self.get_meta()
        ro_fields = getattr(meta, "read_only_fields", [])
        self.flag_fields(fields, ro_fields, "read_only", True)

        wo_fields = getattr(meta, "write_only_fields", [])
        self.flag_fields(fields, wo_fields, "write_only", True)

        pw_fields = getattr(meta, "untrimmed_fields", [])
        self.flag_fields(
            fields,
            pw_fields,
            "trim_whitespace",
            False,
        )

        deferred_fields = self._get_deferred_field_names(fields)
        self.flag_fields(
            fields,
            deferred_fields,
            "deferred",
            True,
        )

        depends = getattr(meta, "depends", {})
        self.change_fields(fields, depends, "depends")

    def _get_flagged_field_names(self, fields, attr, meta_attr=None):
        meta = self.get_meta()
        if meta_attr is None:
            meta_attr = "%s_fields" % attr
        meta_list = set(getattr(meta, meta_attr, []))
        return {
            name
            for name, field in fields.items()
            if getattr(field, attr, None) is True or name in meta_list
        }

    def _get_deferred_field_names(self, fields):
        meta = self.get_meta()
        deferred_fields = self._get_flagged_field_names(fields, "deferred")

        defer_many_relations = (
            settings.DEFER_MANY_RELATIONS
            if not hasattr(meta, "defer_many_relations")
            else meta.defer_many_relations
        )
        if defer_many_relations:
            # Auto-defer all fields, unless the 'deferred' attribute
            # on the field is specifically set to False.
            many_fields = self._get_flagged_field_names(fields, "many")
            deferred_fields.update(
                {
                    name
                    for name in many_fields
                    if getattr(fields[name], "deferred", None) is not False
                }
            )

        return deferred_fields

    def flag_fields(self, all_fields, fields_to_flag, attr, value):
        for name in fields_to_flag:
            field = all_fields.get(name)
            if not field:
                continue
            setattr(field, attr, value)
            field._kwargs[attr] = value

    def change_fields(self, all_fields, fields_dict, attr):
        for key, value in fields_dict.items():
            field = all_fields.get(key)
            if not field:
                continue
            setattr(field, attr, value)
            field._kwargs[attr] = value

    def get_fields(self):
        """Returns the serializer's field set.

        If `dynamic` is True, respects field inclusions/exlcusions.
        Otherwise, reverts back to standard DRF behavior.
        """
        all_fields = self.get_all_fields()
        if self.dynamic is False:
            return all_fields

        if self.id_only():
            return {}

        serializer_fields = copy.deepcopy(all_fields)

        # if the serializer is for metadata, do not remove deferred fields
        if not self.for_metadata:
            request_fields = self.request_fields
            deferred = self._get_deferred_field_names(serializer_fields)

            # apply request overrides
            if request_fields:
                if request_fields is True:
                    request_fields = {}
                for name, include in request_fields.items():
                    if name not in serializer_fields and name != "pk":
                        raise exceptions.ParseError(
                            '"%s" is not a valid field name for "%s".'
                            % (name, self.get_name())
                        )
                    if include is not False and name in deferred:
                        deferred.remove(name)
                    elif include is False:
                        deferred.add(name)

            for name in deferred:
                serializer_fields.pop(name)

        method = self.get_request_method()

        # Toggle read_only flags for immutable fields.
        # Note: This overrides `read_only` if both are set, to allow
        #       inferred DRF fields to be made immutable.

        immutable_field_names = self._get_flagged_field_names(
            serializer_fields, "immutable"
        )

        self.flag_fields(
            serializer_fields,
            immutable_field_names,
            "read_only",
            value=method in ("GET", "PUT", "PATCH"),
        )
        self.flag_fields(
            serializer_fields, immutable_field_names, "immutable", value=True
        )

        # Toggle read_only for only-update fields

        only_update_field_names = self._get_flagged_field_names(
            serializer_fields, "only_update"
        )
        self.flag_fields(
            serializer_fields,
            only_update_field_names,
            "read_only",
            value=method in ("POST"),
        )
        self.flag_fields(
            serializer_fields, only_update_field_names, "only_update", value=True
        )

        # TODO: move this to get_all_fields
        # blocked by DRF field init assertion that read_only and write_only
        # cannot both be true
        meta = self.get_meta()
        hidden_fields = getattr(meta, "hidden_fields", [])
        self.flag_fields(serializer_fields, hidden_fields, "read_only", True)
        self.flag_fields(serializer_fields, hidden_fields, "write_only", True)
        return serializer_fields

    def is_field_sideloaded(self, field_name):
        if not isinstance(self.request_fields, dict):
            return False
        return isinstance(self.request_fields.get(field_name), dict)

    def get_link_fields(self):
        """Construct dict of name:field for linkable fields."""
        if not hasattr(self, "_link_fields"):
            query_params = self.get_request_attribute("query_params", {})
            if "exclude_links" in query_params:
                self._link_fields = {}
            else:
                all_fields = self.get_all_fields()
                self._link_fields = {
                    name: field
                    for name, field in all_fields.items()
                    if isinstance(field, _fields.DynamicRelationField)
                    and getattr(field, "link", True)
                    and not (
                        # Skip sideloaded fields
                        name in self.fields and self.is_field_sideloaded(name)
                    )
                }

        return self._link_fields

    @cached_property
    def _readable_fields(self):
        # NOTE: Copied from DRF, exists in 3.2.x but not 3.1
        return [field for field in self.fields.values() if not field.write_only]

    @cached_property
    def _all_readable_field_names(self):
        fields = self.get_all_fields()
        return [key for key in fields.keys() if not fields[key].write_only]

    @cached_property
    def _readable_field_names(self):
        fields = self.fields
        return [key for key in fields.keys() if not fields[key].write_only]

    def _faster_to_representation(self, instance):
        """Modified to_representation with optimizations.

        1) Returns a plain old dict as opposed to OrderedDict.
            (Constructing ordered dict is ~100x slower than `{}`.)
        2) Ensure we use a cached list of fields
            (this optimization exists in DRF 3.2 but not 3.1)

        Arguments:
            instance: a model instance or data object
        Returns:
            Dict of primitive datatypes.
        """

        ret = {}
        fields = self._readable_fields

        for field in fields:
            try:
                attribute = field.get_attribute(instance)
            except SkipField:
                continue

            if attribute is None:
                # We skip `to_representation` for `None` values so that
                # fields do not have to explicitly deal with that case.
                ret[field.field_name] = None
            else:
                ret[field.field_name] = field.to_representation(attribute)

        return ret

    def is_root(self):
        return self.parent is None

    def to_representation(self, instance):
        """Modified to_representation method.

        Arguments:
            instance: A model instance or data object.
        Returns:
            Instance ID if the serializer is meant to represent its ID.
            Otherwise, a tagged data dict representation.
        """
        id_only = self.id_only()
        if self.get_format() == "admin" and self.is_root():
            id_only = False
        if id_only:
            return instance.pk
        else:
            if self.enable_optimization:
                representation = self._faster_to_representation(instance)
            else:
                representation = super(
                    WithDynamicSerializerMixin, self
                ).to_representation(instance)

            query_params = self.get_request_attribute("query_params", {})
            if settings.ENABLE_LINKS and "exclude_links" not in query_params:
                representation = merge_link_object(self, representation, instance)

        if self.debug:
            representation["_meta"] = {
                "id": instance.pk,
                "type": self.get_plural_name(),
            }

        # tag the representation with the serializer and instance
        return tag_dict(
            representation, serializer=self, instance=instance, embed=self.embed
        )

    def to_internal_value(self, data):
        meta = self.get_meta()
        value = super(WithDynamicSerializerMixin, self).to_internal_value(data)

        id_attr = getattr(meta, "update_lookup_field", "id")
        request_method = self.get_request_method()

        # Add update_lookup_field field back to validated data
        # since super by default strips out read-only fields
        # hence id will no longer be present in validated_data.
        if all(
            (
                isinstance(self.root, DynamicListSerializer),
                id_attr,
                request_method in ("PUT", "PATCH"),
            )
        ):
            id_field = self.fields[id_attr]
            id_value = id_field.get_value(data)
            value[id_attr] = id_value

        return value

    def add_post_save(self, fn):
        if not hasattr(self, "_post_save"):
            self._post_save = []
        self._post_save.append(fn)

    def do_post_save(self, instance):
        if hasattr(self, "_post_save"):
            for fn in self._post_save:
                fn(instance)
            self._post_save = []

    def create(self, validated_data):
        model = self.Meta.model
        meta = Meta(model)
        instance = model()
        to_save = [instance]
        to_set = []
        try:
            with transaction.atomic():
                for attr, value in validated_data.items():
                    try:
                        field = meta.get_field(attr)
                        if field.related_model:
                            if field.many_to_many:
                                to_set.append((instance, attr, value))
                            else:
                                if isinstance(value, dict):
                                    to_save.extend(nested_update(instance, attr, value))
                                else:
                                    if isinstance(
                                        getattr(instance, attr), models.Manager
                                    ):
                                        getattr(instance, attr).set(value)
                                    else:
                                        setattr(instance, attr, value)
                        else:
                            setattr(instance, attr, value)
                    except AttributeError:
                        setattr(instance, attr, value)

                for s in to_save:
                    if self.SET_REQUEST_ON_SAVE:
                        attr = (
                            self.SET_REQUEST_ON_SAVE
                            if isinstance(self.SET_REQUEST_ON_SAVE, str)
                            else "_request"
                        )
                        setattr(s, attr, self.context.get("request"))
                    s.save()

                for i, a, v in to_set:
                    f = getattr(i, a)
                    f.set(v)

        except Exception as e:
            if settings.DEBUG:
                raise
            else:
                raise exceptions.ValidationError(e)

        return instance

    def update(self, instance, validated_data):
        # support nested writes if possible
        meta = Meta(instance)
        to_save = [instance]
        # Simply set each attribute on the instance, and then save it.
        # Note that unlike `.create()` we don't need to treat many-to-many
        # relationships as being a special case. During updates we already
        # have an instance pk for the relationships to be associated with.
        try:
            with transaction.atomic():
                for attr, value in validated_data.items():
                    try:
                        field = meta.get_field(attr)
                        if field.related_model:
                            if isinstance(value, dict):
                                # nested dictionary on a has-one
                                # relationship, we should take the current
                                # related value and apply updates to it
                                to_save.extend(nested_update(instance, attr, value))
                            else:
                                # normal relationship update
                                field = getattr(instance, attr, None)
                                if isinstance(field, models.Manager):
                                    field.set(value)
                                else:
                                    setattr(instance, attr, value)
                        else:
                            setattr(instance, attr, value)
                    except AttributeError:
                        setattr(instance, attr, value)
                    except TypeError as e:
                        if "Direct assignment to the forward side" in str(e):
                            getattr(instance, attr).set(value)
                        else:
                            raise

                for s in to_save:
                    if self.SET_REQUEST_ON_SAVE:
                        attr = (
                            self.SET_REQUEST_ON_SAVE
                            if isinstance(self.SET_REQUEST_ON_SAVE, str)
                            else "_request"
                        )
                        setattr(s, attr, self.context.get("request"))
                    s.save()
        except Exception as e:
            if self.debug:
                raise
            else:
                raise exceptions.ValidationError(e)

        return instance

    def save(self, *args, **kwargs):
        """Serializer save that addresses prefetch issues."""
        update = getattr(self, "instance", None) is not None
        with transaction.atomic():
            try:
                instance = super(WithDynamicSerializerMixin, self).save(*args, **kwargs)
                self.do_post_save(instance)
            except exceptions.APIException:
                if self.debug:
                    import traceback

                    traceback.print_exc()

                raise
            except Exception as e:
                if self.debug:
                    import traceback

                    traceback.print_exc()

                error = e.args[0] if e.args else str(e)
                if not isinstance(error, dict):
                    error = {"error": error}
                self._errors = error
                raise exceptions.ValidationError(self.errors)

        view = self._context.get("view")
        if update and view:
            # Reload the object on update
            # to get around prefetch cache issues
            instance = self.instance = view.get_object()
        return instance

    def id_only(self):
        """Whether the serializer should return an ID instead of an object.

        Returns:
            True if and only if `request_fields` is True.
        """
        return self.dynamic and self.request_fields is True

    @property
    def data(self):
        if getattr(self, "_processed_data", None) is None:
            data = super(WithDynamicSerializerMixin, self).data
            data = SideloadingProcessor(self, data).data if self.envelope else data
            self._processed_data = ReturnDict(data, serializer=self)
        return self._processed_data


class WithDynamicModelSerializerMixin(WithDynamicSerializerMixin):
    """Adds DREST serializer methods specific to model-based serializers."""

    @classmethod
    def get_model(cls):
        return getattr(cls.Meta, "model", None)

    def get_id_fields(self):
        """
        Called to return a list of fields consisting of, at minimum,
        the PK field name. The output of this method is used to
        construct a Prefetch object with a .only() queryset
        when this field is not being sideloaded but we need to
        return a list of IDs.
        """
        model = self.get_model()
        meta = Meta(model)

        out = [meta.get_pk_field().attname]

        # If this is being called, it means it
        # is a many-relation  to its parent.
        # Django wants the FK to the parent,
        # but since accurately inferring the FK
        # pointing back to the parent is less than trivial,
        # we will just pull all ID fields.
        # TODO: We also might need to return all non-nullable fields,
        #    or else it is possible Django will issue another request.
        for field in meta.get_fields():
            if isinstance(field, models.ForeignKey):
                out.append(field.attname)

        return out


class DynamicModelSerializer(
    PermissionsSerializerMixin,
    WithDynamicModelSerializerMixin,
    serializers.ModelSerializer,
):
    """DREST-compatible model-based serializer."""

    serializer_choice_field = _fields.DynamicChoiceField
    serializer_related_field = _fields.DynamicRelationField


for field in (
    "BooleanField",
    "NullBooleanField",
    "CharField",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "EmailField",
    "FilePathField",
    "FloatField",
    "ImageField",
    "BigIntegerField",
    "PositiveIntegerField",
    "PositiveSmallIntegerField",
    "IntegerField",
    "SlugField",
    "FileField",
    "ImageField",
    "TimeField",
    "URLField",
    "UUIDField",
):
    model_field = getattr(models, field, None)
    if model_field:
        serializer_field = "Dynamic%s" % (
            field if "IntegerField" not in field else "IntegerField"
        )
        serializer_field = getattr(_fields, serializer_field, None)
        if serializer_field:
            DynamicModelSerializer.serializer_field_mapping[model_field] = serializer_field

DynamicModelSerializer.serializer_field_mapping[models.TextField] = (
    _fields.DynamicTextField
)

try:
    from django.contrib.postgres import fields as postgres_fields

    DynamicModelSerializer.serializer_field_mapping[postgres_fields.ArrayField] = (
        _fields.DynamicListField
    )
    DynamicModelSerializer.serializer_field_mapping[postgres_fields.JSONField] = (
        _fields.DynamicJSONField
    )
except ImportError:
    pass


if hasattr(models, 'JSONField'):
    DynamicModelSerializer.serializer_field_mapping[models.JSONField] = (
        _fields.DynamicJSONField
    )


class EphemeralObject(object):
    """Object that initializes attributes from a dict."""

    def __init__(self, values_dict):
        if "pk" not in values_dict:
            raise Exception('"pk" key is required')
        self.__dict__.update(values_dict)


class DynamicEphemeralSerializer(WithDynamicSerializerMixin, serializers.Serializer):
    """DREST-compatible baseclass for non-model serializers."""

    def to_representation(self, instance):
        """
        Provides post processing. Sub-classes should implement their own
        to_representation method, but pass the resulting dict through
        this function to get tagging and field selection.

        Arguments:
            instance: Serialized dict, or object. If object,
                it will be serialized by the super class's
                to_representation() method.
        """

        if not isinstance(instance, dict):
            data = super(DynamicEphemeralSerializer, self).to_representation(instance)
        else:
            data = instance
            instance = EphemeralObject(data)

        if self.id_only():
            return data
        else:
            return tag_dict(data, serializer=self, instance=instance)
