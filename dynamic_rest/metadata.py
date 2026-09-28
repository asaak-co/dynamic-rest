"""This module contains custom DRF metadata classes."""
from collections import OrderedDict

import inflection

from rest_framework.exceptions import APIException, ValidationError
from rest_framework.fields import empty
from rest_framework.metadata import SimpleMetadata
from rest_framework.serializers import ListSerializer, ModelSerializer

from dynamic_rest.ephemeral import (
    get_ephemeral_filter_fields,
    get_ephemeral_filter_operators,
    normalize_ephemeral_filter_field,
)
from dynamic_rest.fields import DynamicRelationField, DynamicJSONField, DynamicLinkField
from dynamic_rest.utils import urljoin


def _unwrap_serializer(serializer):
    """Return the child serializer when ``serializer`` is a list serializer."""
    return serializer.child if isinstance(serializer, ListSerializer) else serializer


def get_serializer_field_path_info(serializer, path):
    """Resolve metadata for a dot-separated serializer field path.

    In addition to the leaf field metadata, this records whether any segment
    crosses a to-many relation. Consumers can use that to expose a projected
    leaf as a list-valued field (for example ``loans.name`` becomes a list of
    strings labelled ``Loan Names``).
    """
    serializer = _unwrap_serializer(serializer)
    parts = [part for part in (path or '').split('.') if part]
    if not parts or '.'.join(parts) != path:
        raise ValidationError({'path': 'A valid dot-separated field path is required.'})

    metadata = DynamicMetadata()
    labels = []
    crosses_many = False
    leaf_info = None

    for index, part in enumerate(parts):
        try:
            field = serializer.get_field(part)
        except (AttributeError, ValidationError, KeyError):
            raise ValidationError({path: 'Could not resolve serializer field path.'})

        leaf_info = metadata.get_field_info(field)
        is_last = index == len(parts) - 1
        if is_last:
            crosses_many = crosses_many or leaf_info['type'] == 'many'
            leaf_label = str(
                leaf_info.get('label') or inflection.humanize(part)
            )
            labels.append(
                inflection.pluralize(leaf_label) if crosses_many else leaf_label
            )
            break

        if not isinstance(field, DynamicRelationField):
            raise ValidationError({path: 'Only relation fields may have child fields.'})

        crosses_many = crosses_many or field.many
        relation_label = str(
            getattr(field, 'label', None) or inflection.humanize(part)
        )
        labels.append(inflection.singularize(relation_label))
        serializer = _unwrap_serializer(field.serializer)

    result = OrderedDict(leaf_info)
    leaf_type = leaf_info['type']
    result['path'] = path
    result['field_name'] = parts[-1]
    result['label'] = ' '.join(labels)
    result['many'] = crosses_many
    result['item_type'] = leaf_type if crosses_many else None
    result['type'] = 'list' if crosses_many else leaf_type
    return result


def _get_label(x):
    if isinstance(x, (tuple, list)) and len(x) > 0:
        return x[0]
    if isinstance(x, dict) and 'label' in x:
        return x.get('label')
    return x


def _get_description(x):
    if isinstance(x, (tuple, list)) and len(x) > 1:
        return x[1]
    if isinstance(x, dict) and 'description' in x:
        return x.get('description')
    return None

class DynamicMetadata(SimpleMetadata):
    """A subclass of SimpleMetadata.

    Adds `fields` and `features` to the metdata.
    """

    def determine_actions(self, request, view):
        """Prevent displaying action-specific details."""
        return None

    def get_resource_info(self, serializer, features=None):
        """Build metadata shared by model-backed and ephemeral resources."""
        fields = self.get_serializer_info(serializer)
        self.apply_ephemeral_filter_metadata(serializer, fields)
        try:
            id_field = serializer.get_pk_field()
        except APIException:
            id_field = 'pk'

        resource = {
            'type': 'resource',
            'name': serializer.get_plural_name(),
            'singular': serializer.get_name(),
            'features': features if features is not None else [],
            'section': serializer.get_section(),
            'fields': fields,
            'icon': serializer.get_icon(),
            'search_key': serializer.get_search_key(),
            'style': serializer.get_style(),
            'description': serializer.get_description(),
            'sections': [
                section.serialize() for section in serializer.get_sections()
            ],
            'id_field': id_field,
            'name_field': serializer.get_name_field(),
        }

        meta = serializer.get_meta()
        # A resource role describes its UI purpose. It is independent of the
        # permissions and user roles returned elsewhere in OPTIONS.
        role = getattr(meta, 'role', None)
        if role is not None:
            resource['role'] = role
        role_metadata = getattr(meta, 'role_metadata', None)
        if role_metadata is not None:
            resource['role_metadata'] = role_metadata
        default_fields = getattr(meta, 'default_fields', None)
        default_view = getattr(meta, 'default_view', None)
        if default_fields is not None:
            resource['default_fields'] = list(default_fields)
        if default_view is not None:
            resource['default_view'] = default_view
        elif default_fields is not None:
            resource['default_view'] = {
                'resource': serializer.get_plural_name(),
                'data': {
                    'fields': {
                        field_name: True
                        for field_name in default_fields
                    },
                },
            }
        return resource

    def determine_metadata(self, request, view):
        """Adds `fields` and `features` to the metadata response."""
        metadata = super(DynamicMetadata, self).determine_metadata(request, view)
        metadata['label'] = metadata['name']
        if hasattr(view, 'get_serializer'):
            serializer = view.get_serializer(for_metadata=True)
            metadata.update(
                self.get_resource_info(
                    serializer,
                    features=getattr(view, 'features', []),
                )
            )
            permissions = view.full_permissions
            metadata['permissions'] = permissions.serialize() if permissions else {}
            metadata['permissions']['fields'] = serializer.get_field_permissions()
            metadata['actions'] = [action.serialize() for action in view.actions]

        elif hasattr(view, '_router'):
            metadata['type'] = 'namespace'
            if request.GET.get('all') is not None:
                metadata['resources'] = {
                    name: self.determine_metadata(request, view)
                    for name, view in view._router.get_viewsets(request).items()
                }
            else:
                metadata['resources'] = view._router.get_viewsets(request).keys()

            metadata['url'] = view._router.base_url

        return metadata

    def apply_ephemeral_filter_metadata(self, serializer, fields):
        filter_fields = get_ephemeral_filter_fields(serializer)
        get_model = getattr(serializer, 'get_model', None)
        is_ephemeral = get_model and get_model() is None

        if not filter_fields and not is_ephemeral:
            return fields

        for field_name, field_info in fields.items():
            field = serializer.fields.get(field_name)
            if is_ephemeral:
                if field_info.get('ui') is None:
                    field_info['ui'] = True
                field_info['filterable'] = False
                field_info['sortable'] = bool(getattr(field, 'sortable', False))

            if field_name not in filter_fields:
                continue

            _queryset_field, field_type, operators = normalize_ephemeral_filter_field(
                field_name,
                filter_fields,
            )
            field_info['filterable'] = True
            field_info['filter_type'] = field_type
            field_info['filter_operators'] = sorted(
                get_ephemeral_filter_operators(field_type, operators)
            )

        return fields

    def get_field_info(self, field):
        """Adds to the metadata response."""
        field_info = OrderedDict()
        for out, internal in (
            ('default', 'default'),
            ('label', 'label'),
            ('description', 'help_text'),
            ('null', 'allow_null'),
            ('required', 'required'),
            ('deferred', 'deferred'),
            ('depends', 'depends'),
            ('style', 'style'),
            ('inverse', 'inverse'),
            ('ui', 'ui'),
            ('extra', 'extra')
        ):
            field_info[out] = getattr(field, internal, None)

        if field_info['deferred'] is None:
            field_info['deferred'] = False

        if not field_info['default'] and getattr(field, 'model_field', None) and field.model_field.default:
            field_info['default'] = field.model_field.default
        if field_info['default'] is empty:
            field_info['default'] = None
        if callable(field_info['default']):
            # stringify callable default
            field_info['default'] = f'.{field_info["default"]}'

        if hasattr(field, 'choices'):
            field_info['choices'] = [
                {"id": choice_name, "label": _get_label(choice_value), "description": _get_description(choice_value)}
                for choice_name, choice_value in (
                    field.choices.items()
                    if hasattr(field.choices, 'items')
                    else field.choices
                )
            ]
        if getattr(field, 'choice_parent', None):
            field_info['choice_parent'] = field.choice_parent
            field_info['choice_mapping'] = getattr(field, 'choice_mapping', None)
        if hasattr(field, 'location'):
            field_info['location'] = field.location
        if hasattr(field, 'hide'):
            # should the field be hidden if empty
            field_info['hide'] = field.hide

        many = False
        base_field = field
        if isinstance(field, DynamicRelationField):
            field = field.serializer
        if isinstance(field, ListSerializer):
            field = field.child
            many = True

        if isinstance(field, ModelSerializer):
            type = 'many' if many else 'one'
            field_info['related'] = field.get_plural_name()
            field_info['filter'] = base_field.filter
        else:
            if getattr(field, 'chart', False):
                type = 'chart'
            elif isinstance(field, DynamicJSONField):
                type = 'object'
            elif isinstance(field, DynamicLinkField):
                type = 'iframe' if field.iframe else 'string'
            else:
                type = self.label_lookup[field]

        if type == 'field':
            # assume "string" type if unspecified
            type = 'string'

        if getattr(base_field, 'api_type', None):
            # allow for custom API types, e.g:
            # "resource": the name of an API type
            # "resources": multiple resources
            # "path": a dot-separated path to an API field
            # "paths": multiple paths
            # "filters": a list of JSON-encoded API filters
            # "template": a string with replacements
            type = field.api_type

        if getattr(base_field, 'resource_field', None):
            field_info['resource_field'] = base_field.resource_field

        field_info['type'] = type
        field_info['filterable'] = base_field.source and base_field.source != '*'
        field_info['sortable'] = field_info['filterable'] or (
            getattr(base_field, 'sort_field', None) is not None
        )
        return field_info
