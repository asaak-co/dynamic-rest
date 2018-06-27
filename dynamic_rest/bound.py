from random import randint
import json
from rest_framework.compat import unicode_to_repr
from dynamic_rest.fields import DynamicRelationField


class DynamicBoundField(object):
    """
    A field object that also includes `.value` and `.error` properties
    as well as `.instance`.
    Returned when iterating over a serializer instance,
    providing an API similar to Django forms and form fields.
    """

    def __init__(self, field, value, errors, prefix='', instance=None,
                 id=None):
        self._field = field
        self._prefix = prefix
        rand = ''.join([str(randint(0, 9)) for _ in range(6)])
        self.id = id or rand
        self.value = value
        self.errors = errors
        self.instance = instance
        self.name = prefix + self.field_name

    def __getattr__(self, attr_name):
        return getattr(self._field, attr_name)

    @property
    def _proxy_class(self):
        return self._field.__class__

    def __repr__(self):
        return unicode_to_repr(
            '<%s %s value=%s errors=%s instance=%s>' %
            (self.__class__.__name__, self._field.field_name, self.value,
             self.errors, self.instance))

    def get_rendered_value(self):
        if not hasattr(self, '_rendered_value'):
            if callable(getattr(self._field, 'admin_render', None)):
                self._rendered_value = self._field.admin_render(
                    instance=self.instance, value=self.value)
            else:
                self._rendered_value = self.value
        return self._rendered_value

    def should_render(self):
        value = self.value
        field = self._field

        model_field = getattr(field, 'model_field', None)
        if getattr(model_field, 'primary_key', None):
            return False

        if isinstance(field, DynamicRelationField):
            if field.create:
                return True

        if not value and value != 0 and value != 0.0:
            return False

        return True

    def as_form_field(self):
        value = '' if (self.value is None
                       or self.value is False) else self.value

        parent_name = self._field.parent.get_name()
        rand = ''.join([str(randint(0, 9)) for _ in range(6)])
        id = '%s-%s-%s' % (parent_name, self.name, rand)

        result = self.__class__(self._field, value, self.errors, self._prefix,
                                self.instance, id)
        return result


class DynamicJSONBoundField(DynamicBoundField):
    def as_form_field(self):
        value = self.value
        try:
            value = json.dumps(self.value, sort_keys=True, indent=4)
        except TypeError:
            pass

        parent_name = self._field.parent.get_name()
        rand = ''.join([str(randint(0, 9)) for _ in range(6)])
        id = '%s-%s-%s' % (parent_name, self.name, rand)
        result = self.__class__(self._field, value, self.errors, self._prefix,
                                self.instance, id)
        return result
