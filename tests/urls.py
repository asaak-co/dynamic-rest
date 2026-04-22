
from dynamic_rest.compat import url, include
from dynamic_rest.views import login, logout
from dynamic_rest.routers import DynamicRouter
from dynamic_rest.urls import *  # noqa
from tests import viewsets

router = DynamicRouter()
router.register_resource(viewsets.UserViewSet)
router.register_resource(viewsets.GroupViewSet)
router.register_resource(viewsets.ProfileViewSet)
router.register_resource(viewsets.LocationViewSet)

router.register(r'cars', viewsets.CarViewSet)
router.register(r'cats', viewsets.CatViewSet)
router.register_resource(viewsets.DogViewSet)
router.register_resource(viewsets.HorseViewSet)
router.register_resource(viewsets.PermissionViewSet)
router.register_resource(viewsets.OfficerViewSet)
router.register(r'zebras', viewsets.ZebraViewSet)  # not canonical
router.register(r'user_locations', viewsets.UserLocationViewSet)

# the above routes are duplicated to test versioned prefixes
router.register_resource(viewsets.CatViewSet, namespace='v2')  # canonical
router.register(r'v1/user_locations', viewsets.UserLocationViewSet)
router.register(r'p/users', viewsets.PermissionsUserViewSet, namespace='p')
router.register(r'p/locations', viewsets.PermissionsLocationViewSet, namespace='p')
router.register(
    r'p/locations_wo_users',
    viewsets.PermissionsLocationWriteOnlyUsersViewSet,
    namespace='p',
)
router.register(
    r'p/child_perm_locations',
    viewsets.ChildPermLocationViewSet,
    namespace='p',
)

urlpatterns = [
    url(r'^', include(router.urls)),
    url('login', login),
    url('logout', logout)
]
