from rest_framework import fields
from uuid import UUID
from django.db.models import QuerySet
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.contenttypes.fields import GenericForeignKey
from dynamic_rest.meta import get_model_field, is_field_remote
from dynamic_rest.base import DynamicBase
from dynamic_rest.conf import settings
from dynamic_rest.utils import memoize


def no_assertions_init(self, *args, **kwargs):
    empty = fields.empty
    Field = fields.Field

    self._creation_counter = Field._creation_counter
    Field._creation_counter += 1

    # If `required` is unset, then use `True` unless a default is provided.
    required = kwargs.get('required', None)
    default = kwargs.get('default', empty)
    read_only = kwargs.get('read_only', False)
    validators = kwargs.get('validators', None)
    error_messages = kwargs.get('error_messages', None)

    if required is None:
        required = default is empty and not read_only

    self.read_only = read_only
    self.write_only = kwargs.get('write_only', False)
    self.required = required
    self.default = default
    self.source = kwargs.get('source', None)

    initial = kwargs.get('initial', empty)
    self.initial = self.initial if (initial is empty) else initial
    self.label = kwargs.get('label', None)
    self.help_text = kwargs.get('help_text', None)
    style = kwargs.get('style', None)
    self.style = {} if style is None else style
    self.allow_null = kwargs.get('allow_null', False)

    if self.default_empty_html is not fields.empty:
        if default is not fields.empty:
            self.default_empty_html = default

    if validators is not None:
        self.validators = validators[:]

    # These are set up when the field is added
    self.field_name = None
    self.parent = None

    # Collect default error message from self and parent classes
    messages = {}
    for cls in reversed(self.__class__.__mro__):
        messages.update(getattr(cls, 'default_error_messages', {}))
    messages.update(error_messages or {})
    self.error_messages = messages


fields.Field.__init__ = no_assertions_init


class DynamicField(fields.Field, DynamicBase):

    """
    Generic field base to capture additional custom field attributes.
    """

    def __init__(
        self,
        *args,
        **kwargs
    ):
        """
        Arguments:
            deferred: Whether or not this field is deferred.
                Deferred fields are not included in the response,
                unless explicitly requested.
            field_type: Field data type, if not inferrable from model.
            requires: List of fields that this field depends on.
                Processed by the view layer during queryset build time.
            immutable: True if the field cannot be updated
            only_update: True if the field cannot be set at creation
            get_classes: a parent serializer method name that should
                return a list of classes to apply
            getter: name of a method to call on the parent serializer for
                reading related objects.
                If source is '*', this will default to 'get_$FIELD_NAME'.
            setter: name of a method to call on the parent serializer for
                saving related objects.
                If source is '*', this will default to 'set_$FIELD_NAME'.
            ui: should this field appear in user interfaces?
        """
        self.resource_field = kwargs.pop('resource_field', None)
        self.api_type = kwargs.pop('api_type', None)
        self.num_digits = kwargs.pop('num_digits', 0)
        self.icon = kwargs.pop('icon', None)
        self.requires = kwargs.pop('requires', None)
        self.deferred = kwargs.pop('deferred', None)
        self.field_type = kwargs.pop('field_type', None)
        self.immutable = kwargs.pop('immutable', False)
        self.only_update = kwargs.pop('only_update', False)
        self.get_classes = kwargs.pop('get_classes', None)
        self.getter = kwargs.pop('getter', None)
        self.setter = kwargs.pop('setter', None)
        self.depends = kwargs.pop('depends', None)
        self.hide = kwargs.pop('hide', None)
        self.style = kwargs.pop('style', None) or {}
        self.long = kwargs.pop('long', False)
        self.sort_by = kwargs.pop('sort_by', None)
        self.bound = False
        self.ui = kwargs.pop('ui', True)
        choices = kwargs.get('choices', None)
        if choices:
            self.choices = choices
        if self.getter:
            # dont bind to model
            kwargs['source'] = '*'
            if not self.setter:
                kwargs['read_only'] = True

        self.kwargs = kwargs
        return super(DynamicField, self).__init__(*args, **kwargs)

    def run_validation(self, data):
        if self.setter:
            if data == fields.empty:
                data = [] if self.kwargs.get('many') else None

            def fn(instance):
                setter = getattr(self.parent, self.setter)
                setter(instance, data)

            self.parent.add_post_save(fn)
            raise fields.SkipField()
        return super(DynamicField, self).run_validation(data)

    def bind(self, *args, **kwargs):
        """Bind to the parent serializer."""
        if self.bound:  # Prevent double-binding
            return

        super(DynamicField, self).bind(*args, **kwargs)
        self.bound = True

        source = self.source or self.field_name
        if source == '*':
            if hasattr(self, 'method_name'):
                if self.getter is None:
                    self.getter = self.method_name
                else:
                    self.method_name = self.getter
            if self.getter:
                self.getter = memoize(getattr(self.parent, self.getter), 'pk')
            return

        if self.getter:
            self.getter = memoize(getattr(self.parent, self.getter), 'pk')

        self.source_attrs = source.split('.')

        parent_model = self.parent_model
        if parent_model:
            remote = is_field_remote(parent_model, source)
            model_field = self.model_field
            generic = isinstance(model_field, GenericForeignKey)
            if not generic:
                # Infer, 'default' `required` and `allow_null`
                if 'required' not in self.kwargs and (
                        remote or (
                            model_field and (
                                model_field.has_default() or model_field.null
                            )
                        )
                ):
                    self.required = False
                if 'allow_null' not in self.kwargs and getattr(
                    model_field, 'null', False
                ):
                    self.allow_null = True

                if 'allow_blank' not in self.kwargs and self.allow_null:
                    self.allow_blank = True
                help_text = getattr(model_field, 'help_text', None)

                if 'help_text' not in self.kwargs and help_text:
                    self.help_text = help_text

    def get_format(self):
        return self.parent.get_format()

    def prepare_value(self, instance):
        if instance is None:
            return None
        value = None
        many = self.kwargs.get('many', False)
        getter = self.getter
        if getter:
            # use custom getter to get the value
            if isinstance(instance, list):
                value = [getter(i) for i in instance]
            else:
                value = getter(instance)
        else:
            # use source to get the value
            source = self.source or self.field_name
            sources = source.split('.')
            value = instance
            for source in sources:
                if source == '*':
                    break
                try:
                    value = getattr(value, source)
                except (ObjectDoesNotExist, AttributeError):
                    return None
        if (
            value and
            many and
            callable(getattr(value, 'all', None))
        ):
            # get list from manager
            value = value.all()

        if value and many:
            value = list(value)

        if isinstance(value, QuerySet):
            value = list(value)

        return value

    def admin_get_label(self, instance, value):
        result = value
        if isinstance(result, list):
            return ', '.join([str(r) for r in result])
        return result

    def admin_get_url(self, instance, value):
        serializer = self.parent
        name_field = serializer.get_name_field()
        if name_field == self.field_name:
            return serializer.get_url(instance.pk)

    def admin_get_classes(self, instance, value=None):
        # override this to set custom CSS based on value
        parent = self.parent
        getter = self.get_classes
        if not getter:
            name_field_name = parent.get_name_field()
            if self.field_name == name_field_name:
                getter = parent.get_class_getter()
        if getter and instance and parent:
            return getattr(parent, getter)(instance)
        return None

    def admin_get_icon(self, instance, value):
        if self.icon:
            icon = self.icon
            if callable(self.icon):
                return icon(instance, value)
            else:
                return icon
        else:
            return None

    def admin_render_value(self, value):
        return value

    def admin_render(self, instance, value=None):
        value = self.admin_render_value(value or self.prepare_value(instance))
        if isinstance(value, list) and not isinstance(
            value, str
        ) and not isinstance(value, UUID) and not isinstance(
            instance, list
        ):
            ret = [
                self.admin_render(instance, v)
                for v in value
            ]
            return '<br/> '.join(ret)

        # URL link or None
        url = self.admin_get_url(instance, value)
        # list of classes or None
        classes = self.admin_get_classes(instance, value) or []
        classes.append('drest-value')
        classes = [x for x in classes if x]
        # name of an icon or None
        icon = self.admin_get_icon(instance, value)
        # label or None
        label = self.admin_get_label(instance, value)
        if not label:
            label = ''

        tag = 'a' if url else 'span'
        result = label or value
        if result is None:
            result = ''
        if icon:
            result = u"""
                <span>
                    <span class="{0} {0}-{1}"></span>
                    <span>{2}</span>
                </span>
            """.format(
                settings.ADMIN_ICON_PACK,
                icon,
                result
            )

        if url:
            url = 'href="%s"' % url
        else:
            url = ''

        result = u'<{0} {3} class="{1}">{2}</{0}>'.format(
            tag,
            ' '.join(classes),
            result,
            url
        )

        return result

    def to_internal_value(self, value):
        try:
            return super(DynamicField, self).to_internal_value(value)
        except NotImplementedError:
            return value

    def get_attribute(self, instance):
        if self.getter and not hasattr(self, 'method_name'):
            return self.getter(instance)
        else:
            return super(DynamicField, self).get_attribute(instance)

    def to_representation(self, value):
        try:
            return super(DynamicField, self).to_representation(value)
        except NotImplementedError:
            return value

    @property
    def parent_model(self):
        if not hasattr(self, '_parent_model'):
            parent = self.parent
            if isinstance(parent, fields.ListField):
                parent = parent.parent
            if parent:
                self._parent_model = getattr(parent.Meta, 'model', None)
            else:
                return None
        return self._parent_model

    @property
    def sort_field(self):
        if self.sort_by:
            if not hasattr(self, '_sort_field'):
                source = self.sort_by
                try:
                    self._sort_field = get_model_field(
                        self.parent_model,
                        source
                    )
                except AttributeError:
                    self._sort_field = None
            return self._sort_field
        else:
            return self.model_field

    @property
    def model_field(self):
        if not hasattr(self, '_model_field'):
            source = self.source or self.field_name
            try:
                self._model_field = get_model_field(
                    self.parent_model,
                    source
                )
            except AttributeError:
                self._model_field = None
        return self._model_field


class DynamicComputedField(DynamicField):
    def __init__(self, *args, **kwargs):
        kwargs['read_only'] = True
        super(DynamicComputedField, self).__init__(*args, **kwargs)


class CountField(DynamicComputedField):

    """
    Computed field that counts the number of elements in another field.
    """

    def __init__(self, serializer_source, *args, **kwargs):
        """
        Arguments:
            serializer_source: A serializer field.
            unique: Whether or not to perform a count of distinct elements.
        """
        self.field_type = int
        # Use `serializer_source`, which indicates a field at the API level,
        # instead of `source`, which indicates a field at the model level.
        self.serializer_source = serializer_source
        # Set `source` to an empty value rather than the field name to avoid
        # an attempt to look up this field.
        kwargs['source'] = '*'
        self.unique = kwargs.pop('unique', True)
        return super(CountField, self).__init__(*args, **kwargs)

    def get_attribute(self, obj):
        source = self.serializer_source
        try:
            field = self.parent.fields[source]
        except Exception:
            return None

        value = field.get_attribute(obj)
        data = field.to_representation(value)

        # How to count None is undefined... let the consumer decide.
        if data is None:
            return None

        # Check data type. Technically len() works on dicts, strings, but
        # since this is a "count" field, we'll limit to list, set, tuple.
        if not isinstance(data, (list, set, tuple)):
            raise TypeError(
                '"%s" is %s (%s). '
                "Must be list, set or tuple to be countable." % (
                    source, data, type(data)
                )
            )

        if self.unique:
            # Try to create unique set. This may fail if `data` contains
            # non-hashable elements (like dicts).
            try:
                data = set(data)
            except TypeError:
                pass

        return len(data)


class WithRelationalFieldMixin(object):
    """Mostly code shared by DynamicRelationField and
        DynamicGenericRelationField.
    """

    def _get_request_fields_from_parent(self):
        """Get request fields from the parent serializer."""
        if not self.parent:
            return None

        if not getattr(self.parent, 'request_fields'):
            return None

        if not isinstance(self.parent.request_fields, dict):
            return None

        return self.parent.request_fields.get(self.field_name)
