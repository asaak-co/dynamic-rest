from django.conf import settings as django_settings
from django.test.signals import setting_changed
from django.utils.module_loading import import_string


IMPORT_STRINGS = {
    'API_GET_HOME'
}

DYNAMIC_REST = {
    # API_NAME: Name of the API
    'API_NAME': 'DREST',

    # API_DESCRIPTION: Description of the API
    'API_DESCRIPTION': 'My DREST API',

    # API_ROOT_SECURE: whether or not the root API view requires authentication
    'API_ROOT_SECURE': False,

    # API_ROOT_URL: API root URL
    'API_ROOT_URL': '/',

    'API_GET_HOME': None,

    # ALL_FIELDS_ON_UPDATE: set to true to include all deferred fields
    # back automatically on POST, PUT, and PATCH requests
    'ALL_FIELDS_ON_UPDATE': True,

    # ADDITIONAL_PRIMARY_RESOURCE_PREFIX: String to prefix additional
    # instances of the primary resource when sideloading.
    'ADDITIONAL_PRIMARY_RESOURCE_PREFIX': '+',

    # ADMIN_TEMPLATE: Name of the admin template
    # Override this to add custom styling or UI
    'ADMIN_TEMPLATE': 'dynamic_rest/admin.html',

    # ADMIN_DATETIME_FORMAT: controls datetime field render
    'ADMIN_DATETIME_FORMAT': 'YYYY-MM-DD HH:mm',

    # ADMIN_DATE_FORMAT: controls date field render
    'ADMIN_DATE_FORMAT': 'YYYY-MM-DD',

    # ADMIN_LOGIN_TEMPLATE: the login template used to render login UI
    'ADMIN_LOGIN_TEMPLATE': 'dynamic_rest/login.html',

    # ADMIN_LOGIN_URL: the login URL, defaults to reverse-URL lookup
    'ADMIN_LOGIN_URL': '',

    # ADMIN_LOGOUT_URL: the logout URL, defaults to reverse-URL lookup
    'ADMIN_LOGOUT_URL': '',

    # ADMIN_ICON_PACK: the admin icon pack, either fa or mdi
    'ADMIN_ICON_PACK': 'mdi',

    'CURSOR_QUERY_PARAM': 'cursor',

    'CURSOR_ORDER_QUERY_PARAM': 'cursor.order',

    # DEBUG: enable/disable internal debugging
    'DEBUG': False,

    # DEFER_MANY_RELATIONS: automatically defer many-relations, unless
    # `deferred=False` is explicitly set on the field.
    'DEFER_MANY_RELATIONS': False,

    # ENABLE_FILTERED_RELATION: should FilteredRelation be used for more accurate filtering
    # through related fields
    # downside it has some bugs in Django < 5
    'ENABLE_FILTERED_RELATION': True,

    # ENABLE_BROWSABLE_API: enable/disable the browsable API.
    # It can be useful to disable it in production.
    'ENABLE_BROWSABLE_API': True,

    # ENABLE_BULK_PARTIAL_CREATION: enable/disable partial creation in bulk
    'ENABLE_BULK_PARTIAL_CREATION': False,

    # ENABLE_BULK_UPDATE: enable/disable update in bulk
    'ENABLE_BULK_UPDATE': True,

    # ENABLE_LINKS: enable/disable relationship links
    'ENABLE_LINKS': True,

    # Enables host-relative links.  Only compatible with resources registered
    # through the dynamic router.  If a resource doesn't have a canonical
    # path registered, links will default back to being resource-relative urls
    'ENABLE_HOST_RELATIVE_LINKS': True,

    # ENABLE_RELATED_LINKS: adds relationship links onto the response
    'ENABLE_RELATED_LINKS': True,

    # ENABLE_SELF_LINKS: enable/disable links to self
    'ENABLE_SELF_LINKS': True,

    # ENABLE_SERIALIZER_CACHE: enable/disable caching of related serializers
    'ENABLE_SERIALIZER_CACHE': True,

    # ENABLE_SERIALIZER_OPTIMIZATIONS: enable/disable representation speedups
    'ENABLE_SERIALIZER_OPTIMIZATIONS': True,

    # ENABLE_ALL_FIELDS_CACHE: serializer optimization
    'ENABLE_ALL_FIELDS_CACHE': True,

    # EXCLUDE_COUNT_QUERY_PARAM: global setting for the query parameter
    # that disables counting during PageNumber pagination
    'EXCLUDE_COUNT_QUERY_PARAM': 'exclude_count',

    # MAX_PAGE_SIZE: global setting for max page size.
    # Can be overriden at the viewset level.
    'MAX_PAGE_SIZE': None,

    # SET_REQUEST_ON_SAVE: global setting for whether request should be set
    # on saved instances.
    # Can be overridden at the viewset level.
    'SET_REQUEST_ON_SAVE': False,

    # ONE_SERIALIZER_PER_MODEL: setting to True will cause
    # internal serializer/model lookups to defer to the local serializer
    'ONE_SERIALIZER_PER_MODEL': False,

    # PAGE_QUERY_PARAM: global setting for the pagination query parameter.
    # Can be overriden at the viewset level.
    'PAGE_QUERY_PARAM': 'page',

    # PAGE_SIZE: global setting for page size.
    # Can be overriden at the viewset level.
    'PAGE_SIZE': None,

    # PAGE_SIZE_QUERY_PARAM: global setting for the page size query parameter.
    # Can be overriden at the viewset level.
    'PAGE_SIZE_QUERY_PARAM': 'per_page',
}


class Settings(object):

    def __init__(self, name, defaults, settings):
        self.name = name
        self.defaults = defaults
        self.keys = set(defaults.keys())

        self._cache = {}
        self._reload(getattr(settings, self.name, {}))

        setting_changed.connect(self._settings_changed)

    def _reload(self, value):
        """Reload settings after a change."""
        self.settings = value
        self._cache = {}

    def __getattr__(self, attr):
        """Get a setting."""
        if attr not in self._cache:

            if attr not in self.keys:
                raise AttributeError("Invalid API setting: '%s'" % attr)

            if attr in self.settings:
                val = self.settings[attr]
            else:
                val = self.defaults[attr]

            if attr in IMPORT_STRINGS and isinstance(val, str):
                val = import_string(val)

            # Cache the result
            self._cache[attr] = val

        return self._cache[attr]

    def _settings_changed(self, *args, **kwargs):
        """Handle changes to core settings."""
        setting, value = kwargs['setting'], kwargs['value']
        if setting == self.name:
            self._reload(value)


settings = Settings('DYNAMIC_REST', DYNAMIC_REST, django_settings)
