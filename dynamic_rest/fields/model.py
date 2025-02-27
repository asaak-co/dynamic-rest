import sys
from .base import DynamicField
from rest_framework import serializers

for cls_name in (
    'BooleanField',
    'NullBooleanField',
    'DecimalField',
    'DictField',
    'EmailField',
    'FilePathField',
    'FloatField',
    'HiddenField',
    'IPAddressField',
    'IntegerField',
    'ListField',
    'RegexField',
    'SlugField',
    'TimeField',
    'URLField',
    'UUIDField',
):
    cls = getattr(serializers, cls_name, None)
    if not cls:
        continue

    new_name = 'Dynamic%s' % cls_name
    new_cls = type(
        new_name,
        (DynamicField, cls),
        {}
    )
    setattr(sys.modules[__name__], new_name, new_cls)

if not hasattr(sys.modules[__name__], 'DynamicNullBooleanField'):
    setattr(sys.modules[__name__], 'DynamicNullBooleanField', sys.modules[__name__].DynamicBooleanField)

class DynamicMethodField(
    DynamicField,
    serializers.SerializerMethodField
):
    pass
