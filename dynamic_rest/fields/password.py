from rest_framework.serializers import CharField
from .base import DynamicField


class DynamicPasswordField(
    CharField,
    DynamicField
):
    """A string field whose value is a secret.

    Reported to clients with the "password" API type so that UIs can
    mask the value and offer a reveal control.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('api_type', 'password')
        super(DynamicPasswordField, self).__init__(*args, **kwargs)
