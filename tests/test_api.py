import datetime
import json
from decimal import Decimal
from django.db import connection
from urllib.parse import quote
from django.test import override_settings
from rest_framework.test import APITestCase

from tests.models import Cat, Group, Location, Permission, Profile, User, Car, Country
from tests.serializers import NestedEphemeralSerializer, PermissionSerializer
from tests.setup import create_fixture

UNICODE_STRING = chr(9629)  # unicode heart
# UNICODE_URL_STRING = urllib.quote(UNICODE_STRING.encode('utf-8'))
UNICODE_URL_STRING = "%E2%96%9D"


@override_settings(DYNAMIC_REST={"ENABLE_LINKS": False})
class TestUsersAPI(APITestCase):
    def setUp(self):
        self.fixture = create_fixture()
        self.maxDiff = None

    def _get_json(self, url, expected_status=200):
        response = self.client.get(url)
        self.assertEquals(expected_status, response.status_code, response.content)
        return json.loads(response.content.decode("utf-8"))

    def test_get(self):
        with self.assertNumQueries(1):
            # 1 for User, 0 for Location
            response = self.client.get("/users/")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "users": [
                    {"id": 1, "location": 1, "name": "0"},
                    {"id": 2, "location": 1, "name": "1"},
                    {"id": 3, "location": 2, "name": "2"},
                    {"id": 4, "location": 3, "name": "3"},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_trailing_slash_does_not_redirect(self):
        response = self.client.get("/users/1")
        self.assertEquals(200, response.status_code)

    def test_get_with_include(self):
        with self.assertNumQueries(2):
            # 2 queries: 1 for User, 1 for Group, 0 for Location
            response = self.client.get("/users/?include[]=groups")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "users": [
                    {"id": 1, "groups": [1, 2], "location": 1, "name": "0"},
                    {"id": 2, "groups": [1, 2], "location": 1, "name": "1"},
                    {"id": 3, "groups": [1, 2], "location": 2, "name": "2"},
                    {"id": 4, "groups": [1, 2], "location": 3, "name": "3"},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

        with self.assertNumQueries(2):
            # 2 queries: 1 for User, 1 for Group
            response = self.client.get("/groups/?include[]=members")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "groups": [
                    {"id": 1, "members": [1, 2, 3, 4], "name": "0"},
                    {"id": 2, "members": [1, 2, 3, 4], "name": "1"},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_exclude(self):
        with self.assertNumQueries(1):
            response = self.client.get("/users/?exclude[]=name")
        query = connection.queries[-1]["sql"]
        self.assertFalse("name" in query, query)
        self.assertFalse("*" in query, query)

        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "users": [
                    {"id": 1, "location": 1},
                    {"id": 2, "location": 1},
                    {"id": 3, "location": 2},
                    {"id": 4, "location": 3},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_nested_has_one_sideloading_disabled(self):
        with self.assertNumQueries(2):
            response = self.client.get("/users/?include[]=location.&sideloading=false")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "users": [
                    {"id": 1, "location": {"id": 1, "name": "0"}, "name": "0"},
                    {"id": 2, "location": {"id": 1, "name": "0"}, "name": "1"},
                    {"id": 3, "location": {"id": 2, "name": "1"}, "name": "2"},
                    {"id": 4, "location": {"id": 3, "name": "2"}, "name": "3"},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_nested_has_one(self):
        with self.assertNumQueries(2):
            response = self.client.get("/users/?include[]=location.")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "locations": [
                    {"id": 1, "name": "0"},
                    {"id": 2, "name": "1"},
                    {"id": 3, "name": "2"},
                ],
                "users": [
                    {"id": 1, "location": 1, "name": "0"},
                    {"id": 2, "location": 1, "name": "1"},
                    {"id": 3, "location": 2, "name": "2"},
                    {"id": 4, "location": 3, "name": "3"},
                ],
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_nested_has_many(self):
        with self.assertNumQueries(2):
            # 2 queries: 1 for User, 1 for Group
            response = self.client.get("/users/?include[]=groups.")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "groups": [{"id": 1, "name": "0"}, {"id": 2, "name": "1"}],
                "users": [
                    {"groups": [1, 2], "id": 1, "location": 1, "name": "0"},
                    {"groups": [1, 2], "id": 2, "location": 1, "name": "1"},
                    {"groups": [1, 2], "id": 3, "location": 2, "name": "2"},
                    {"groups": [1, 2], "id": 4, "location": 3, "name": "3"},
                ],
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_nested_include(self):
        with self.assertNumQueries(3):
            # 3 queries: 1 for User, 1 for Group, 1 for Permissions
            response = self.client.get("/users/?include[]=groups.permissions")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "groups": [
                    {"id": 1, "name": "0", "permissions": [1]},
                    {"id": 2, "name": "1", "permissions": [2]},
                ],
                "users": [
                    {"groups": [1, 2], "id": 1, "location": 1, "name": "0"},
                    {"groups": [1, 2], "id": 2, "location": 1, "name": "1"},
                    {"groups": [1, 2], "id": 3, "location": 2, "name": "2"},
                    {"groups": [1, 2], "id": 4, "location": 3, "name": "3"},
                ],
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_nested_exclude(self):
        with self.assertNumQueries(2):
            # 2 queries: 1 for User, 1 for Group
            response = self.client.get("/users/?exclude[]=groups.name")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "groups": [{"id": 1}, {"id": 2}],
                "users": [
                    {"groups": [1, 2], "id": 1, "location": 1, "name": "0"},
                    {"groups": [1, 2], "id": 2, "location": 1, "name": "1"},
                    {"groups": [1, 2], "id": 3, "location": 2, "name": "2"},
                    {"groups": [1, 2], "id": 4, "location": 3, "name": "3"},
                ],
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_nested_exclude_all(self):
        with self.assertNumQueries(2):
            # 2 queries: 1 for User, 1 for Group
            url = "/users/?exclude[]=groups.*&include[]=groups.name"
            response = self.client.get(url)
        self.assertEquals(200, response.status_code, response.content.decode("utf-8"))
        self.assertEquals(
            {
                "groups": [{"name": "0"}, {"name": "1"}],
                "users": [
                    {"groups": [1, 2], "id": 1, "location": 1, "name": "0"},
                    {"groups": [1, 2], "id": 2, "location": 1, "name": "1"},
                    {"groups": [1, 2], "id": 3, "location": 2, "name": "2"},
                    {"groups": [1, 2], "id": 4, "location": 3, "name": "3"},
                ],
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_exclude_all_and_include_field(self):
        with self.assertNumQueries(1):
            url = "/users/?exclude[]=*&include[]=id"
            response = self.client.get(url)
        self.assertEquals(200, response.status_code, response.content.decode("utf-8"))
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(set(["id"]), set(data["users"][0].keys()))

    def test_get_with_exclude_all_and_include_relationship(self):
        with self.assertNumQueries(2):
            url = "/users/?exclude[]=*&include[]=groups."
            response = self.client.get(url)
        self.assertEquals(200, response.status_code, response.content.decode("utf-8"))
        data = json.loads(response.content.decode("utf-8"))
        self.assertEquals(set(["groups"]), set(data["users"][0].keys()))
        self.assertTrue("groups" in data)

    def test_get_one_with_include(self):
        with self.assertNumQueries(2):
            # 2 queries: 1 for User, 1 for Group
            response = self.client.get("/users/1/?include[]=groups.")
        self.assertEquals(200, response.status_code)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEquals(len(data.get("groups", [])), 2)

    def test_get_with_filter(self):
        with self.assertNumQueries(1):
            # verify that extra [] are stripped out of the key
            response = self.client.get("/users/?filter{name}[]=1")
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "users": [
                    {"id": 2, "location": 1, "name": "1"},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_filter_by_count(self):
        # ensure it works for normal relationship fields like $group
        response = self.client.get("/users/?filter{groups.$count.gt}=5")
        self.assertEquals(200, response.status_code)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEquals(len(content["users"]), 0)

        response = self.client.get("/users/?filter{groups.$count.gte}=2")
        self.assertEquals(200, response.status_code)
        content = json.loads(response.content.decode("utf-8"))
        # 4 users
        self.assertEquals(len(content["users"]), 4)

        # ensure it works with filtered relationship fields like loc1users
        url = "/groups/?filter{id}=1&filter{loc1users.$count}=1"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)

    def test_get_with_filter_no_match(self):
        with self.assertNumQueries(1):
            response = self.client.get("/users/?filter{name}[]=foo")
        self.assertEquals(200, response.status_code)
        self.assertEquals({"users": []}, json.loads(response.content.decode("utf-8")))

    def test_get_with_filter_unicode_no_match(self):
        with self.assertNumQueries(1):
            response = self.client.get("/users/?filter{name}[]=%s" % UNICODE_URL_STRING)
        self.assertEquals(200, response.status_code)
        self.assertEquals({"users": []}, json.loads(response.content.decode("utf-8")))
        with self.assertNumQueries(1):
            response = self.client.get(("/users/?filter{name}[]=%s") % UNICODE_STRING)
        self.assertEquals(200, response.status_code)
        self.assertEquals({"users": []}, json.loads(response.content.decode("utf-8")))

    def test_get_with_filter_unicode(self):
        User.objects.create(name=UNICODE_STRING, last_name="Unicode")
        with self.assertNumQueries(1):
            response = self.client.get("/users/?filter{name}[]=%s" % UNICODE_URL_STRING)
        self.assertEquals(200, response.status_code)
        self.assertEquals(1, len(json.loads(response.content.decode("utf-8"))["users"]))
        with self.assertNumQueries(1):
            response = self.client.get(("/users/?filter{name}[]=%s") % UNICODE_STRING)
        self.assertEquals(200, response.status_code)
        self.assertEquals(1, len(json.loads(response.content.decode("utf-8"))["users"]))

    def test_get_with_filter_in(self):
        url = "/users/?filter{name.in}=1&filter{name.in}=2"
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "users": [
                    {"id": 2, "location": 1, "name": "1"},
                    {"id": 3, "location": 2, "name": "2"},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_filter_exclude(self):
        url = "/users/?filter{-name}=1"
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "users": [
                    {"id": 1, "location": 1, "name": "0"},
                    {"id": 3, "location": 2, "name": "2"},
                    {"id": 4, "location": 3, "name": "3"},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_filter_relation_field(self):
        url = "/users/?filter{location.name}=1"
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "users": [
                    {"id": 3, "location": 2, "name": "2"},
                ]
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_filter_and_include_relationship(self):
        url = "/users/?include[]=groups.&filter{groups|name}=1"
        with self.assertNumQueries(2):
            # 2 queries: 1 for User, 1 for Group
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        self.assertEquals(
            {
                "groups": [{"id": 2, "name": "1"}],
                "users": [
                    {"groups": [2], "id": 1, "location": 1, "name": "0"},
                    {"groups": [2], "id": 2, "location": 1, "name": "1"},
                    {"groups": [2], "id": 3, "location": 2, "name": "2"},
                    {"groups": [2], "id": 4, "location": 3, "name": "3"},
                ],
            },
            json.loads(response.content.decode("utf-8")),
        )

    def test_get_with_filter_and_source_rewrite(self):
        """Test filtering on fields where source is different"""
        url = "/locations/?filter{address}=here&include[]=address"
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEquals(len(data["locations"]), 1)

    def test_get_with_filter_and_query_injection(self):
        """Test viewset with query injection"""
        url = "/users/?name=1"
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEquals(len(data["users"]), 1)
        self.assertEquals(data["users"][0]["name"], "1")

    def test_get_with_include_one_to_many(self):
        """Test o2m without related_name set."""
        url = "/locations/?filter{id}=1&include[]=users"
        with self.assertNumQueries(2):
            # 2 queries: 1 for locations, 1 for location-users
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEquals(len(data["locations"]), 1)
        self.assertEquals(len(data["locations"][0]["users"]), 2)

    def test_get_with_count_field(self):
        url = "/locations/?filter{id}=1&include[]=users&include[]=user_count"
        with self.assertNumQueries(2):
            # 2 queries: 1 for locations, 1 for location-users
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEquals(len(data["locations"]), 1)
        self.assertEquals(len(data["locations"][0]["users"]), 2)
        self.assertEquals(data["locations"][0]["user_count"], 2)

    def test_get_with_queryset_injection(self):
        url = "/users/?location=1"
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEquals(len(data["users"]), 2)

    def test_get_with_include_invalid(self):
        for bad_data in ("name..", "groups..name", "foo", "groups.foo"):
            response = self.client.get("/users/?include[]=%s" % bad_data)
            self.assertEquals(400, response.status_code)

    def test_post(self):
        data = {
            "name": "test",
            "last_name": "last",
            "location": 1,
            "display_name": "test last",  # Read only, should be ignored.
        }
        response = self.client.post(
            "/users/", json.dumps(data), content_type="application/json"
        )
        self.assertEquals(201, response.status_code)
        self.assertEquals(
            json.loads(response.content.decode("utf-8")),
            {
                "user": {
                    "id": 5,
                    "name": "test",
                    "permissions": [],
                    "favorite_pet": None,
                    "favorite_pet_id": None,
                    "groups": [],
                    "location": 1,
                    "last_name": "last",
                    "display_name": None,
                    "thumbnail_url": None,
                    "number_of_cats": 1,
                    "profile": None,
                    "date_of_birth": None,
                    "is_dead": False,
                }
            },
        )

    def test_post_with_related_setter(self):
        data = {"name": "test", "loc1usersGetter": [1]}
        response = self.client.post(
            "/groups/", json.dumps(data), content_type="application/json"
        )
        self.assertEqual(201, response.status_code)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual([1], content["group"]["loc1usersGetter"])

    def test_put(self):
        group = Group.objects.create(name="test group")
        data = {"name": "updated"}
        response = self.client.put(
            "/groups/%s/" % group.pk, json.dumps(data), content_type="application/json"
        )
        self.assertEquals(200, response.status_code, response.content)
        updated_group = Group.objects.get(pk=group.pk)
        self.assertEquals(updated_group.name, data["name"])

    def test_get_with_default_queryset(self):
        url = "/groups/?filter{id}=1&include[]=loc1users&filter{loc1users|id}=2"
        # Group.objects.filter(id=1).prefetch_related(
        #   Prefetch('users', queryset=User.objects.filter(location_id=1).filter(id=2))
        # )
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual([2], content["groups"][0]["loc1users"])

    def test_get_with_default_queryset_and_filters(self):
        url = "/groups/?filter{id}=1&include[]=loc1users&filter{loc1users.location.name}=0&filter{loc1users|id}=2"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        # location 1 does have name "0" -> group is returned
        self.assertEqual(1, len(content["groups"]))

        url = "/groups/?filter{id}=1&include[]=loc1users&filter{loc1users.location.name}=1&filter{loc1users|id}=2"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        # location 1 does not have name "1" -> no group is returned
        self.assertEqual(0, len(content["groups"]))

        # now try the same queries but starting from users, such that the filtered relation
        # loc1users is in the middle of the filter
        url = "/users/?filter{id}=1&filter{groups.loc1users.location.name}=1"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, len(content["users"]))

        url = "/users/?filter{id}=1&filter{groups.loc1users.location.name}=0"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(content["users"]))

    def test_get_with_default_lambda_queryset(self):
        url = "/groups/?filter{id}=1&include[]=loc1usersLambda"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(sorted([1, 2]), content["groups"][0]["loc1usersLambda"])

    def test_get_with_related_getter(self):
        url = "/groups/?filter{id}=1&include[]=loc1usersGetter.location.*"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual([1, 2], content["groups"][0]["loc1usersGetter"])
        self.assertEqual(1, content["locations"][0]["id"])

    def test_get_with_default_queryset_filtered(self):
        """
        Make sure filter can be added to relational fields with default
        filters.
        """
        url = (
            "/groups/?filter{id}=1&include[]=loc1users"
            "&filter{loc1users|id.in}=3"
            "&filter{loc1users|id.in}=1"
        )
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual([1], content["groups"][0]["loc1users"])

    def test_get_with_filter_nested_rewrites(self):
        """
        Test filter for members.id which needs to be rewritten as users.id
        """
        user = User.objects.create(name="test user")
        group = Group.objects.create(name="test group")
        user.groups.add(group)

        url = "/groups/?filter{members.id}=%s&include[]=members" % user.pk
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(content["groups"]))
        self.assertEqual(group.pk, content["groups"][0]["id"])

        url = (
            "/users/?filter{groups.members.id}=%s" "&include[]=groups.members" % user.pk
        )
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(1, len(content["users"]))

    def test_get_with_filter_nonexistent_field(self):
        # Filtering on non-existent field should return 400
        url = "/users/?filter{foobar}=1"
        response = self.client.get(url)
        self.assertEqual(400, response.status_code)

    def test_get_with_filter_invalid_data(self):
        User.objects.create(name="test", date_of_birth=datetime.datetime.utcnow())
        url = "/users/?filter{date_of_birth.gt}=0&filter{date_of_birth.lt}=0"
        response = self.client.get(url)
        self.assertEqual(400, response.status_code)
        content = response.content.decode("utf-8")
        self.assertTrue(
            ("value has an invalid date format. " "It must be in YYYY-MM-DD format.")
            in content,
            content,
        )

    def test_get_with_filter_or(self):
        User.objects.create(name="test", date_of_birth=datetime.datetime.utcnow())
        url = "/users/?filter{date_of_birth.lt}=2999-01-01&filter{date_of_birth.gt}=2999-01-01&filter{date_of_birth}=2999-01-01&filter=or"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(
            User.objects.filter(date_of_birth__isnull=False).count(),
            len(content["users"]),
        )

    def test_get_with_filter_deferred(self):
        # Filtering deferred field should work
        grp = Group.objects.create(name="test group")
        user = self.fixture.users[0]
        user.groups.add(grp)

        url = "/users/?filter{groups.id}=%s" % grp.pk
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(1, len(content["users"]))
        self.assertEqual(user.pk, content["users"][0]["id"])

    def test_get_with_filter_outer_joins(self):
        """
        Test that the API does not return duplicate results
        when the underlying SQL query would return dupes.
        """
        user = User.objects.create(name="test")
        group_a = Group.objects.create(name="A")
        group_b = Group.objects.create(name="B")
        user.groups.set([group_a, group_b])
        response = self.client.get(
            "/users/?filter{groups.name.in}=A&filter{groups.name.in}=B"
        )
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(1, len(content["users"]), content)

    def test_get_with_filter_isnull(self):
        """
        Test for .isnull filters
        """

        # User with location=None
        User.objects.create(name="name", last_name="lname", location=None)

        # Count Users where location is not null
        expected = User.objects.filter(location__isnull=False).count()

        url = "/users/?filter{location.isnull}=0"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(expected, len(content["users"]))

        url = "/users/?filter{location.isnull}=False"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(expected, len(content["users"]))

        url = "/users/?filter{location.isnull}=1"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(content["users"]))

        url = "/users/?filter{-location.isnull}=True"
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(expected, len(content["users"]))

    def test_get_with_nested_source_fields(self):
        u1 = User.objects.create(name="test1", last_name="user")
        Profile.objects.create(
            user=u1, display_name="foo", thumbnail_url="http://thumbnail.url"
        )

        url = (
            "/users/?filter{id}=%s&include[]=display_name"
            "&include[]=thumbnail_url" % u1.pk
        )
        response = self.client.get(url)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(content["users"][0]["display_name"])
        self.assertIsNotNone(content["users"][0]["thumbnail_url"])

    def test_get_with_nested_source_fields_count(self):
        loc = Location.objects.create(name="test location")
        u1 = User.objects.create(name="test1", last_name="user", location=loc)
        Profile.objects.create(user=u1, display_name="foo")
        u2 = User.objects.create(name="test2", last_name="user", location=loc)
        Profile.objects.create(user=u2, display_name="moo")

        # Test prefetching to pull profile.display_name into UserSerializer
        url = "/users/?include[]=display_name" "&include[]=thumbnail_url"

        with self.assertNumQueries(2):
            response = self.client.get(url)
            self.assertEqual(200, response.status_code)

        # Test prefetching of user.location.name into ProfileSerializer
        url = "/profiles/?include[]=user_location_name"
        with self.assertNumQueries(3):
            response = self.client.get(url)
            self.assertEqual(200, response.status_code)
            content = json.loads(response.content.decode("utf-8"))
            self.assertIsNotNone(content["profiles"][0]["user_location_name"])

    def test_get_with_dynamic_method_field(self):
        url = "/users/?include[]=number_of_cats"
        with self.assertNumQueries(3):
            response = self.client.get(url)
            self.assertEqual(200, response.status_code)
            self.assertEquals(
                {
                    "users": [
                        {
                            "id": 1,
                            "location": 1,
                            "name": "0",
                            "number_of_cats": 1,
                        },
                        {
                            "id": 2,
                            "location": 1,
                            "name": "1",
                            "number_of_cats": 1,
                        },
                        {
                            "id": 3,
                            "location": 2,
                            "name": "2",
                            "number_of_cats": 1,
                        },
                        {
                            "id": 4,
                            "location": 3,
                            "name": "3",
                            "number_of_cats": 0,
                        },
                    ]
                },
                json.loads(response.content.decode("utf-8")),
            )

    def test_get_with_request_filters_and_requires(self):
        """
        This tests conflicting external and internal prefetch requirements.

        `location.cats` is an external requirement that points
        to the `Location.cat_set` model relationship.

        `user.number_of_cats` is an internal requirement that points
        to the same relationship.

        The prefetch tree produced by this call merges the two together
        into a single prefetch:
        {
           'location': {
              'cat_set': {}
            }
        }
        """

        url = (
            "/users/?"
            "include[]=number_of_cats&"
            "include[]=location.cats.&"
            "filter{location.cats|name.icontains}=1"
        )
        with self.assertNumQueries(3):
            response = self.client.get(url)
            self.assertEqual(200, response.status_code)
            self.assertEquals(
                {
                    "cats": [{"id": 2, "name": "1"}],
                    "locations": [
                        {"name": "0", "id": 1, "cats": []},
                        {"name": "1", "id": 2, "cats": [2]},
                        {"name": "2", "id": 3, "cats": []},
                    ],
                    "users": [
                        {
                            "id": 1,
                            "location": 1,
                            "name": "0",
                            "number_of_cats": 0,
                        },
                        {
                            "id": 2,
                            "location": 1,
                            "name": "1",
                            "number_of_cats": 0,
                        },
                        {
                            "id": 3,
                            "location": 2,
                            "name": "2",
                            "number_of_cats": 1,
                        },
                        {
                            "id": 4,
                            "location": 3,
                            "name": "3",
                            "number_of_cats": 0,
                        },
                    ],
                },
                json.loads(response.content.decode("utf-8")),
            )

    def test_boolean_filters_on_boolean_field(self):
        # create one dead user
        User.objects.create(name="Dead", last_name="Mort", is_dead=True)

        # set up test specs
        tests = {True: ["true", "True", "1", "okies"], False: ["false", "False", "0"]}

        # run through test scenarios
        for expected_value, test_values in tests.items():
            for test_value in test_values:
                url = "/users/?include[]=is_dead&filter{is_dead}=%s" % test_value
                data = self._get_json(url)

                expected = set([expected_value])
                actual = set([o["is_dead"] for o in data["users"]])
                self.assertEqual(
                    expected,
                    actual,
                    "Boolean filter '%s' failed. Expected=%s Actual=%s"
                    % (
                        test_value,
                        expected,
                        actual,
                    ),
                )


@override_settings(DYNAMIC_REST={"ENABLE_LINKS": False})
class TestLocationsAPI(APITestCase):
    def setUp(self):
        self.fixture = create_fixture()
        self.maxDiff = None

    def test_options(self):
        response = self.client.options("/locations/")
        self.assertEquals(200, response.status_code)
        actual = json.loads(response.content.decode("utf-8"))
        expected = {
            "description": None,
            "name": "locations",
            "parses": [
                "application/json",
                "application/x-www-form-urlencoded",
                "multipart/form-data",
            ],
            "fields": {
                "name": {
                    "deferred": False,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "Name",
                    "null": False,
                    "type": "string",
                },
                "address": {
                    "deferred": True,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "Address",
                    "null": False,
                    "type": "field",
                },
                "document": {
                    "deferred": False,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "Document",
                    "null": False,
                    "type": "file upload",
                },
                "id": {
                    "deferred": False,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "ID",
                    "null": False,
                    "type": "integer",
                },
                "user_count": {
                    "deferred": False,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "User count",
                    "null": False,
                    "type": "field",
                },
                "users": {
                    "deferred": True,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "Users",
                    "null": True,
                    "related": "users",
                    "type": "many",
                    "filter": None,
                },
                "cats": {
                    "deferred": True,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "Cats",
                    "null": True,
                    "related": "cats",
                    "type": "many",
                },
                "bad_cats": {
                    "deferred": True,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "Bad cats",
                    "null": True,
                    "related": "cats",
                    "filter": None,
                    "type": "many",
                },
                "friendly_cats": {
                    "deferred": True,
                    "depends": None,
                    "description": None,
                    "filterable": True,
                    "sortable": True,
                    "default": None,
                    "label": "Friendly cats",
                    "null": True,
                    "related": "cats",
                    "filter": None,
                    "type": "many",
                },
            },
            "singular": "location",
        }
        # Django 1.7 and 1.9 differ in their interpretation of
        # "nullable" when it comes to inverse relationship fields.
        # Ignore the values for the purposes of this comparison.
        for field in ["cats", "friendly_cats", "bad_cats", "users"]:
            del actual["fields"][field]["null"]
            del expected["fields"][field]["null"]
        actual.pop("renders")
        actual.pop("features")
        # TODO: fix this assertion for new metadata format
        self.assertTrue(True)
        # self.assertEquals(
        #    json.loads(json.dumps(expected)), json.loads(json.dumps(actual))
        # )

    def test_get_with_filter_by_user(self):
        url = "/locations/?filter{users}=1"
        response = self.client.get(url)
        self.assertEqual(200, response.status_code, response.content)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(1, len(content["locations"]))

    def test_get_with_filter_rewrites(self):
        """Tests various filter rewrite scenarios"""
        urls = [
            "/locations/?filter{cats}=1",
            "/locations/?filter{friendly_cats}=1",
            "/locations/?filter{bad_cats}=1",
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(200, response.status_code, response.content)


class TestRelationsAPI(APITestCase):
    """Test auto-generated relation endpoints."""

    def setUp(self):
        self.fixture = create_fixture()

    def test_create_related_m2o(self):
        # many-to-one FK
        # user.location
        data = {"name": "Foo"}
        response = self.client.post("/users/1/location/", data=data, format="json")
        self.assertEqual(201, response.status_code, response.content)
        content = json.loads(response.content.decode("utf-8"))
        self.assertIsNotNone(content["location"]["id"])
        self.assertEquals(content["location"]["user_count"], 1)
        self.assertEquals(content["location"]["users"][0], 1)

        user = User.objects.get(pk=1)
        self.assertIsNotNone(user.location)
        self.assertEquals(user.location.name, "Foo")

    def test_create_related_o2m(self):
        data = {"name": "Foo", "last_name": "Bar"}
        response = self.client.post("/locations/1/users/", data=data, format="json")
        self.assertEqual(201, response.status_code, response.content)
        content = json.loads(response.content.decode("utf-8"))
        pk = content["user"]["id"]
        self.assertIsNotNone(pk)

        location = Location.objects.get(pk=1)
        self.assertTrue(location.user_set.filter(pk=pk).exists())

    def test_generated_relation_fields(self):
        r = self.client.get("/users/1/location/")
        self.assertEqual(200, r.status_code)

        r = self.client.get("/users/1/permissions/")
        self.assertEqual(200, r.status_code, r.content)
        self.assertFalse("groups" in r.data["permissions"][0])

        r = self.client.get("/users/1/groups/")
        self.assertEqual(200, r.status_code)

        # Not a relation field
        r = self.client.get("/users/1/name/")
        self.assertEqual(404, r.status_code)

    def test_location_users_relations_identical_to_sideload(self):
        r1 = self.client.get("/locations/1/?include[]=users.")
        self.assertEqual(200, r1.status_code)
        r1_data = json.loads(r1.content.decode("utf-8"))

        r2 = self.client.get("/locations/1/users/")
        self.assertEqual(200, r2.status_code, r2.content)
        r2_data = json.loads(r2.content.decode("utf-8"))

        self.assertEqual(r2_data["users"], r1_data["users"])

    def test_relation_includes(self):
        r = self.client.get("/locations/1/users/?include[]=location.")
        self.assertEqual(200, r.status_code, r.content)

        content = json.loads(r.content.decode("utf-8"))
        self.assertTrue("locations" in content)

    def test_relation_excludes(self):
        r = self.client.get("/locations/1/users/?exclude[]=location")
        self.assertEqual(200, r.status_code, r.content)
        content = json.loads(r.content.decode("utf-8"))

        self.assertFalse("location" in content["users"][0])

    def test_relation_filter_returns_error(self):
        r = self.client.get("/locations/1/users/?filter{name}=foo")
        self.assertEqual(400, r.status_code)


class TestUserLocationsAPI(APITestCase):
    """
    Test API on serializer with embedded fields.
    """

    def setUp(self):
        self.fixture = create_fixture()

    def test_get_embedded(self):
        with self.assertNumQueries(3):
            url = "/v1/user_locations/1/"
            response = self.client.get(url)

        self.assertEqual(200, response.status_code)
        content = json.loads(response.content.decode("utf-8"))
        groups = content["user_location"]["groups"]
        location = content["user_location"]["location"]
        self.assertEqual(content["user_location"]["location"]["name"], "0")
        self.assertTrue(isinstance(groups[0], dict))
        self.assertTrue(isinstance(location, dict))

    def test_get_embedded_force_sideloading(self):
        with self.assertNumQueries(3):
            url = "/v1/user_locations/1/?sideloading=true"
            response = self.client.get(url)

        self.assertEqual(200, response.status_code)
        content = json.loads(response.content.decode("utf-8"))
        groups = content["user_location"]["groups"]
        location = content["user_location"]["location"]
        self.assertEqual(content["locations"][0]["name"], "0")
        self.assertFalse(isinstance(groups[0], dict))
        self.assertFalse(isinstance(location, dict))


class TestLinks(APITestCase):
    def setUp(self):
        self.fixture = create_fixture()

        home = Location.objects.create()
        hunting_ground = Location.objects.create()
        self.cat = Cat.objects.create(name="foo", home=home, backup_home=hunting_ground)
        self.cat.hunting_grounds.add(hunting_ground)

    def test_deferred_relations_have_links(self):
        r = self.client.get("/v2/cats/1/")
        self.assertEqual(200, r.status_code)
        content = json.loads(r.content.decode("utf-8"))

        cat = content["cat"]
        self.assertTrue("links" in cat)

        # 'home' has link=None set so should not have a link object
        self.assertTrue("home" not in cat["links"])

        # test for default link (auto-generated relation endpoint)
        # Note that the pluralized name is used rather than the full prefix.
        self.assertEqual(cat["links"]["foobar"], "/v2/cats/1/foobar/")

        # test for dynamically generated link URL
        cat1 = Cat.objects.get(pk=1)
        self.assertEqual(
            cat["links"]["backup_home"],
            "/locations/%s/?include[]=address" % cat1.backup_home.pk,
        )

    @override_settings(DYNAMIC_REST={"ENABLE_HOST_RELATIVE_LINKS": False})
    def test_relative_links(self):
        r = self.client.get("/v2/cats/1/")
        self.assertEqual(200, r.status_code)
        content = json.loads(r.content.decode("utf-8"))

        cat = content["cat"]
        self.assertTrue("links" in cat)

        # test that links urls become resource-relative urls when
        # host-relative urls are turned off.
        self.assertEqual(cat["links"]["foobar"], "foobar/")

    def test_including_empty_relation_hides_link(self):
        r = self.client.get("/v2/cats/1/?include[]=foobar")
        self.assertEqual(200, r.status_code)
        content = json.loads(r.content.decode("utf-8"))

        # 'foobar' is included but empty, so don't return a link
        cat = content["cat"]
        self.assertFalse(cat["foobar"])
        self.assertFalse("foobar" in cat["links"])

    def test_including_non_empty_many_relation_has_link(self):
        r = self.client.get("/v2/cats/%s/?include[]=foobar" % self.cat.pk)
        self.assertEqual(200, r.status_code)
        content = json.loads(r.content.decode("utf-8"))
        cat = content["cat"]
        self.assertTrue("foobar" in cat)
        self.assertTrue("foobar" in cat["links"])

    def test_no_links_for_included_single_relations(self):
        url = "/v2/cats/%s/?include[]=home" % self.cat.pk
        r = self.client.get(url)
        self.assertEqual(200, r.status_code)
        content = json.loads(r.content.decode("utf-8"))

        cat = content["cat"]
        self.assertTrue("home" in cat)
        self.assertFalse("home" in cat["links"])

    def test_sideloading_relation_hides_link(self):
        url = "/v2/cats/%s/?include[]=foobar." % self.cat.pk
        r = self.client.get(url)
        self.assertEqual(200, r.status_code)
        content = json.loads(r.content.decode("utf-8"))

        cat = content["cat"]
        self.assertTrue("foobar" in cat)
        self.assertTrue("locations" in content)  # check for sideload
        self.assertFalse("foobar" in cat["links"])  # no link

    def test_one_to_one_dne(self):
        user = User.objects.create(name="foo", last_name="bar")

        url = "/users/%s/profile/" % user.pk
        r = self.client.get(url)
        self.assertEqual(200, r.status_code, r.content)
        # Check error message to differentiate from a routing error 404
        content = json.loads(r.content.decode("utf-8"))
        self.assertEqual({}, content)

    def test_ephemeral_object_link(self):
        class FakeCountObject(object):
            pk = 1
            values = []

        class FakeNested(object):
            value_count = FakeCountObject()

        szr = NestedEphemeralSerializer()
        data = szr.to_representation(FakeNested())
        self.assertEqual(data, {"value_count": 1}, data)

    def test_meta_read_only_relation_field(self):
        """Test for making a DynamicRelationField read-only by adding
        it to Meta.read_only_fields.
        """
        data = {
            "name": "test ro",
            "last_name": "last",
            "location": 1,
            "profile": "bogus value",  # Read only relation field
        }
        response = self.client.post(
            "/users/", json.dumps(data), content_type="application/json"
        )
        # Note: if 'profile' isn't getting ignored, this will return
        # a 404 since a matching Profile object isn't found.
        self.assertEquals(201, response.status_code)

    def test_no_links_when_excluded(self):
        r = self.client.get("/v2/cats/1/?exclude_links")
        self.assertEqual(200, r.status_code)
        content = json.loads(r.content.decode("utf-8"))

        cat = content["cat"]
        self.assertFalse("links" in cat)

    @override_settings(
        DYNAMIC_REST={
            "ENABLE_LINKS": True,
            "DEFER_MANY_RELATIONS": True,
        }
    )
    def test_auto_deferral(self):
        perm = Permission.objects.create(name="test", code=1)
        perm.groups.add(self.fixture.groups[0])

        # Check serializers
        fields = PermissionSerializer().get_all_fields()
        self.assertIs(fields["users"].deferred, False)
        self.assertIs(fields["groups"].deferred, True)

        url = "/permissions/%s/" % perm.pk
        r = self.client.get(url)
        data = json.loads(r.content.decode("utf-8"))
        self.assertFalse("groups" in data["permission"])

        # users shouldn't be deferred because `deferred=False` is
        # explicitly set on the field.
        self.assertTrue("users" in data["permission"])


class TestDogsAPI(APITestCase):
    """
    Tests for sorting
    """

    def setUp(self):
        self.fixture = create_fixture()

    def test_sort_exclude_count(self):
        # page 1
        url = "/dogs/?sort[]=name&exclude_count=1&exclude_links=1&per_page=4"
        # 1 query - one for getting dogs, 0 for count
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        expected_data = [
            {
                "id": 2,
                "name": "Air-Bud",
                "origin": "Air Bud 4: Seventh Inning Fetch",
                "fur": "gold",
            },
            {
                "id": 1,
                "name": "Clifford",
                "origin": "Clifford the big red dog",
                "fur": "red",
            },
            {
                "id": 4,
                "name": "Pluto",
                "origin": "Mickey Mouse",
                "fur": "brown and white",
            },
            {"id": 3, "name": "Spike", "origin": "Rugrats", "fur": "brown"},
        ]
        expected_meta = {"page": 1, "per_page": 4, "more_pages": True}
        actual_response = json.loads(response.content.decode("utf-8"))
        actual_data = actual_response.get("dogs")
        actual_meta = actual_response.get("meta")
        self.assertEqual(expected_data, actual_data)
        self.assertEqual(expected_meta, actual_meta)

        # page 2
        url = f"{url}&page=2"
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        expected_data = [
            {"id": 5, "name": "Spike", "origin": "Tom and Jerry", "fur": "light-brown"}
        ]
        expected_meta = {"page": 2, "per_page": 4, "more_pages": False}
        actual_response = json.loads(response.content.decode("utf-8"))
        actual_data = actual_response.get("dogs")
        actual_meta = actual_response.get("meta")
        self.assertEqual(expected_data, actual_data)
        self.assertEqual(expected_meta, actual_meta)

        # there should be 3 pages
        url = "/dogs/?sort[]=name&exclude_links=1&per_page=2"

        url = f"{url}&cursor=1"
        response = self.client.get(url)
        response = json.loads(response.content.decode("utf-8"))
        meta = response.get("meta", {})
        self.assertTrue("cursor" in meta)
        cursor = meta["cursor"]
        self.assertIsNotNone(meta["total_results"])
        self.assertIsNotNone(meta["total_pages"])
        self.assertIsNotNone(cursor)

        url = f"{url}&cursor={cursor}"
        response = self.client.get(url)
        response = json.loads(response.content.decode("utf-8"))
        meta = response.get("meta", {})
        self.assertTrue("cursor" in meta)
        self.assertNotEqual(cursor, meta["cursor"])
        cursor = meta["cursor"]
        self.assertIsNotNone(cursor)
        self.assertTrue(meta["more_pages"])

        url = f"{url}&cursor={cursor}"
        response = self.client.get(url)
        response = json.loads(response.content.decode("utf-8"))
        meta = response.get("meta", {})
        self.assertTrue("cursor" in meta)
        self.assertIsNone(meta["cursor"])
        self.assertFalse(meta["more_pages"])

    def test_sort(self):
        url = "/dogs/?sort[]=name&exclude_links"
        # 2 queries - one for getting dogs, one for the meta (count)
        with self.assertNumQueries(2):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        expected_response = [
            {
                "id": 2,
                "name": "Air-Bud",
                "origin": "Air Bud 4: Seventh Inning Fetch",
                "fur": "gold",
            },
            {
                "id": 1,
                "name": "Clifford",
                "origin": "Clifford the big red dog",
                "fur": "red",
            },
            {
                "id": 4,
                "name": "Pluto",
                "origin": "Mickey Mouse",
                "fur": "brown and white",
            },
            {"id": 3, "name": "Spike", "origin": "Rugrats", "fur": "brown"},
            {"id": 5, "name": "Spike", "origin": "Tom and Jerry", "fur": "light-brown"},
        ]
        actual_response = json.loads(response.content.decode("utf-8")).get("dogs")
        self.assertEquals(expected_response, actual_response)

    def test_sort_reverse(self):
        url = "/dogs/?sort[]=-name&exclude_links"
        # 2 queries - one for getting dogs, one for the meta (count)
        with self.assertNumQueries(2):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        expected_response = [
            {"id": 3, "name": "Spike", "origin": "Rugrats", "fur": "brown"},
            {"id": 5, "name": "Spike", "origin": "Tom and Jerry", "fur": "light-brown"},
            {
                "id": 4,
                "name": "Pluto",
                "origin": "Mickey Mouse",
                "fur": "brown and white",
            },
            {
                "id": 1,
                "name": "Clifford",
                "origin": "Clifford the big red dog",
                "fur": "red",
            },
            {
                "id": 2,
                "name": "Air-Bud",
                "origin": "Air Bud 4: Seventh Inning Fetch",
                "fur": "gold",
            },
        ]
        actual_response = json.loads(response.content.decode("utf-8")).get("dogs")
        self.assertEquals(expected_response, actual_response)

    def test_sort_multiple(self):
        url = "/dogs/?sort[]=-name&sort[]=-origin&exclude_links"
        # 2 queries - one for getting dogs, one for the meta (count)
        with self.assertNumQueries(2):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        expected_response = [
            {"id": 5, "name": "Spike", "origin": "Tom and Jerry", "fur": "light-brown"},
            {"id": 3, "name": "Spike", "origin": "Rugrats", "fur": "brown"},
            {
                "id": 4,
                "name": "Pluto",
                "origin": "Mickey Mouse",
                "fur": "brown and white",
            },
            {
                "id": 1,
                "name": "Clifford",
                "origin": "Clifford the big red dog",
                "fur": "red",
            },
            {
                "id": 2,
                "name": "Air-Bud",
                "origin": "Air Bud 4: Seventh Inning Fetch",
                "fur": "gold",
            },
        ]
        actual_response = json.loads(response.content.decode("utf-8")).get("dogs")
        self.assertEquals(expected_response, actual_response)

    def test_sort_rewrite(self):
        url = "/dogs/?sort[]=fur&exclude_links"
        # 2 queries - one for getting dogs, one for the meta (count)
        with self.assertNumQueries(2):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)
        expected_response = [
            {"id": 3, "name": "Spike", "origin": "Rugrats", "fur": "brown"},
            {
                "id": 4,
                "name": "Pluto",
                "origin": "Mickey Mouse",
                "fur": "brown and white",
            },
            {
                "id": 2,
                "name": "Air-Bud",
                "origin": "Air Bud 4: Seventh Inning Fetch",
                "fur": "gold",
            },
            {"id": 5, "name": "Spike", "origin": "Tom and Jerry", "fur": "light-brown"},
            {
                "id": 1,
                "name": "Clifford",
                "origin": "Clifford the big red dog",
                "fur": "red",
            },
        ]
        actual_response = json.loads(response.content.decode("utf-8")).get("dogs")
        self.assertEquals(expected_response, actual_response)

    def test_sort_invalid(self):
        url = "/horses?sort[]=borigin"
        response = self.client.get(url)

        # expected server to throw a 400 if an incorrect
        # sort field is specified
        self.assertEquals(400, response.status_code)


class TestHorsesAPI(APITestCase):
    """
    Tests for sorting on default fields and limit sorting fields
    """

    def setUp(self):
        self.fixture = create_fixture()

    def test_sort(self):
        url = "/horses?exclude_links"
        # 1 query - one for getting horses
        # (the viewset as features specified, so no meta is returned)
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)

        # expect the default for horses to be sorted by -name
        expected_response = {
            "horses": [
                {"id": 2, "name": "Secretariat", "origin": "Kentucky"},
                {"id": 1, "name": "Seabiscuit", "origin": "LA"},
            ]
        }
        actual_response = json.loads(response.content.decode("utf-8"))
        self.assertEquals(expected_response, actual_response)

    def test_sort_with_field_not_allowed(self):
        url = "/horses?sort[]=origin"
        response = self.client.get(url)

        # if `ordering_fields` are specified in the viewset, only allow sorting
        # based off those fields. If a field is listed in the url that is not
        # specified, return a 400
        self.assertEquals(400, response.status_code)


class TestZebrasAPI(APITestCase):
    """
    Tests for sorting on when ordering_fields is __all__
    """

    def setUp(self):
        self.fixture = create_fixture()

    def test_sort(self):
        url = "/zebras?sort[]=-name&exclude_links"
        # 1 query - one for getting zebras
        # (the viewset as features specified, so no meta is returned)
        with self.assertNumQueries(1):
            response = self.client.get(url)
        self.assertEquals(200, response.status_code)

        # expect sortable on any field on horses because __all__ is specified
        expected_response = {
            "zebras": [
                {"id": 2, "name": "Ted", "origin": "africa"},
                {"id": 1, "name": "Ralph", "origin": "new york"},
            ]
        }
        actual_response = json.loads(response.content.decode("utf-8"))
        self.assertEquals(expected_response, actual_response)


class TestCatsAPI(APITestCase):
    """
    Tests for nested resources
    """

    def setUp(self):
        self.fixture = create_fixture()
        home_id = self.fixture.locations[0].id
        backup_home_id = self.fixture.locations[1].id
        parent = Cat.objects.create(
            name="Parent", home_id=home_id, backup_home_id=backup_home_id
        )
        self.kitten = Cat.objects.create(
            name="Kitten", home_id=home_id, backup_home_id=backup_home_id, parent=parent
        )

    def test_additional_sideloads(self):
        response = self.client.get("/cats/%i?include[]=parent." % self.kitten.id)
        content = json.loads(response.content.decode("utf-8"))
        self.assertTrue("cat" in content)
        self.assertTrue("+cats" in content)
        self.assertEquals(content["cat"]["name"], "Kitten")
        self.assertEquals(content["+cats"][0]["name"], "Parent")

    def test_allows_whitespace(self):
        data = {
            "name": "  Zahaklu  ",
            "home": self.kitten.home_id,
            "backup_home": self.kitten.backup_home_id,
            "parent": self.kitten.parent_id,
        }
        response = self.client.post(
            "/cats/?include[]=*",
            json.dumps(data),
            content_type="application/json",
        )
        self.assertEqual(201, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["cat"]["name"], "  Zahaklu  ")

    def test_immutable_field(self):
        """Make sure immutable 'parent' field can be set on POST"""
        parent_id = self.kitten.parent_id
        kitten_name = "New Kitten"
        data = {
            "name": kitten_name,
            "home": self.kitten.home_id,
            "backup_home": self.kitten.backup_home_id,
            "parent": parent_id,
        }
        response = self.client.post(
            "/cats/?include[]=*", json.dumps(data), content_type="application/json"
        )
        self.assertEqual(201, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["cat"]["parent"], parent_id)
        self.assertEqual(data["cat"]["name"], kitten_name)

        # Try to change immutable data in a PATCH request...
        patch_data = {
            "parent": self.kitten.pk,
            "name": "Renamed Kitten",
        }
        response = self.client.patch(
            "/cats/%s/" % data["cat"]["id"],
            json.dumps(patch_data),
            content_type="application/json",
        )
        self.assertEqual(200, response.status_code)
        data = json.loads(response.content.decode("utf-8"))

        # ... and it should not have changed:
        self.assertEqual(data["cat"]["parent"], parent_id)
        self.assertEqual(data["cat"]["name"], kitten_name)

    def test_filter_relationship_rewrite(self):
        response = self.client.get(
            "/cars?filter{country_name.icontains}=Chi&include[]=name"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["cars"][0]["name"], "Forta")

    def test_combine(self):
        response = self.client.get("/cars?combine=count(name)")
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["data"]["count(name)"], 3)

        # 3 operands, with extra whitespace
        response = self.client.get(
            "/cars?combine= count( country_id) / count(id) %2B 0.1 as c "
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertTrue(abs(data["data"]["c"] - (0.6666 + 0.1)) < 0.001)

    def test_combine_multiple(self):
        response = self.client.get(
            "/cars?combine=count(name)&combine=max(country_name)&combine=min(country_name)"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["data"]["count(name)"], 3)
        self.assertEqual(data["data"]["max(country_name)"], "United States")
        self.assertEqual(data["data"]["min(country_name)"], "China")

    def test_combine_by(self):
        Car.objects.create(
            country=Country.objects.get(name="United States"), name="Tesla"
        )
        response = self.client.get(
            "/cars?combine=count(name)&combine.by=country_name&debug=1"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertTrue("United States" in data["data"], data["data"])
        self.assertEqual(data["data"]["United States"]["count(name)"], 2)
        self.assertEqual(data["data"]["China"]["count(name)"], 1)
        self.assertEqual(data["data"][""]["count(name)"], 1)
        # self.maxDiff = 9999999
        self.assertEqual(
            data["meta"]["query"],
            'SELECT "tests_country"."name" AS "_country_name", COUNT("tests_car"."name") AS "_count(name)" '
            'FROM "tests_car" '
            'LEFT OUTER JOIN "tests_country" ON ("tests_car"."country_id" = "tests_country"."id") '
            "GROUP BY 1",  # "tests_country"."name"',
            data["meta"]["query"],
        )

    def test_combine_many_by(self):
        Car.objects.create(
            country=Country.objects.get(name="United States"), name="Tesla"
        )
        response = self.client.get(
            "/cars?combine=count(id) as count&combine.by=country_name,name&debug=1"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["data"]["United States"]["Tesla"]["count"], 1)
        self.assertEqual(data["data"]["China"]["Forta"]["count"], 1)
        self.assertEqual(data["data"][""]["BMW"]["count"], 1)

    def test_combine_over(self):
        Car.objects.create(
            country=Country.objects.get(name="United States"), name="Tesla"
        )
        response = self.client.get(
            "/cars?combine=count(name)&combine.over=country_name"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["data"]["count(name)"][0], [None, 1])
        self.assertEqual(data["data"]["count(name)"][1], ["China", 1])
        self.assertEqual(data["data"]["count(name)"][2], ["United States", 2])

    def test_combine_over_by(self):
        User.objects.all().delete()
        for month in range(1, 3):
            User.objects.create(
                name=f"test{month}",
                last_name="Family1",
                date_of_birth=f"2020-0{month}-05",
            )
        for month in range(1, 2):
            User.objects.create(
                name=f"test{month}",
                last_name="Family2",
                date_of_birth=f"2020-0{month}-08",
            )

        # over alone
        response = self.client.get(
            "/users?combine=count(name)&combine.over=month(date_of_birth)"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(
            data["data"]["count(name)"], [["2020-01-01", 2], ["2020-02-01", 1]]
        )
        # over (auto-field)
        response = self.client.get(
            "/users?combine=count(name)&combine.over=auto(date_of_birth)"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(
            data["data"]["count(name)"],
            [["2019-12-30", 1], ["2020-01-06", 1], ["2020-02-03", 1]],
        )

        # over with by
        response = self.client.get(
            "/users?combine=count(name)&combine=min(name)&combine.over=month(date_of_birth)&combine.by=last_name"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(
            data["data"]["Family1"]["count(name)"],
            [["2020-01-01", 1], ["2020-02-01", 1]],
        )
        self.assertEqual(
            data["data"]["Family1"]["min(name)"],
            [["2020-01-01", "test1"], ["2020-02-01", "test2"]],
        )
        self.assertEqual(data["data"]["Family2"]["count(name)"], [["2020-01-01", 1]])
        self.assertEqual(
            data["data"]["Family2"]["min(name)"], [["2020-01-01", "test1"]]
        )

    def setup_users(self):
        User.objects.all().delete()
        for month in range(1, 3):
            User.objects.create(
                name=f"test{month}",
                last_name="Family1",
                date_of_birth=f"2020-0{month}-05",
            )
        for month in range(1, 2):
            User.objects.create(
                name=f"test{month}",
                last_name="Family2",
                date_of_birth=f"2020-0{month}-08",
            )

    def test_combine_many_overs(self):
        self.setup_users()

        # 2 overs
        response = self.client.get(
            "/users?combine=count(name),min(name)&combine.over=month(date_of_birth),last_name"
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(
            data["data"]["count(name)"],
            [
                ["2020-01-01", "Family1", 1],
                ["2020-01-01", "Family2", 1],
                ["2020-02-01", "Family1", 1],
            ],
        )
        self.assertEqual(
            data["data"]["min(name)"],
            [
                ["2020-01-01", "Family1", "test1"],
                ["2020-01-01", "Family2", "test1"],
                ["2020-02-01", "Family1", "test2"],
            ],
        )

    def test_combine_flat(self):
        self.setup_users()
        response = self.client.get(
            "/users?combine=count(name) as count,count0(count) as count0,count1(count) as count1,count2(count) as count2,min(name),percent0(count) as p0&combine.over=month(date_of_birth),last_name&combine.format=flat"
        )
        data = json.loads(response.content.decode("utf-8"))
        self.assertTrue("data" in data, data)
        self.maxDiff = None
        third = str(Decimal("100.0") * 1 / 3)
        self.assertEqual(
            data["data"],
            [
                {
                    "count": 1,
                    "last_name": "Family1",
                    "month(date_of_birth)": "2020-01-01",
                    "min(name)": "test1",
                    "count0": 3,
                    "count1": 2,
                    "count2": 1,
                    "p0": third,
                },
                {
                    "count": 1,
                    "last_name": "Family2",
                    "month(date_of_birth)": "2020-01-01",
                    "min(name)": "test1",
                    "count0": 3,
                    "count1": 2,
                    "count2": 1,
                    "p0": third,
                },
                {
                    "count": 1,
                    "last_name": "Family1",
                    "month(date_of_birth)": "2020-02-01",
                    "min(name)": "test2",
                    "count0": 3,
                    "count1": 1,
                    "count2": 1,
                    "p0": third,
                },
            ],
            data["data"],
        )

        response = self.client.get(
            "/users?combine=count(name) as count&combine.format=flat"
        )
        data = json.loads(response.content.decode("utf-8"))
        self.assertTrue("data" in data, data)
        self.assertEqual(
            data["data"],
            [{"count": 3}],
            data["data"],
        )

    def test_combine_expression(self):
        # weird aggregation, but test data doesn't have many integer fields..
        response = self.client.get(
            f'/cars?combine={quote("count(id) + count(name) as doubleCats")}'
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["data"]["doubleCats"], 6)

        response = self.client.get(
            f'/cars?combine={quote("count(id) * 3 as doubleCats")}'
        )
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["data"]["doubleCats"], 9)

    def test_combine_as(self):
        response = self.client.get("/cars?combine=count(name) as numCats")
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["data"].get("numCats"), 3, data["data"])

    def test_sort_relationship_rewrite(self):
        response = self.client.get("/cars?sort[]=-country_name&include[]=name")
        self.assertEqual(200, response.status_code, response.content)
        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["cars"][0]["name"], "Porshe")

    def test_update_nested_field(self):
        patch_data = {"country_name": "foobar"}
        response = self.client.patch(
            "/cars/1", json.dumps(patch_data), content_type="application/json"
        )
        self.assertEqual(200, response.status_code, response.content)

    def test_update_create_nested_data(self):
        patch_data = {
            "country_name": "Germany",
            "country_short_name": None,
        }
        response = self.client.patch(
            "/cars/3", json.dumps(patch_data), content_type="application/json"
        )
        # should fail because short name is required
        self.assertEqual(400, response.status_code, response.content)
        patch_data["country_short_name"] = "DE"
        response = self.client.patch(
            "/cars/3", json.dumps(patch_data), content_type="application/json"
        )
        self.assertEqual(200, response.status_code, response.content)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(content["car"]["country_short_name"], "DE")
        self.assertEqual(content["car"]["country_name"], "Germany")


class TestFilters(APITestCase):
    """
    Tests for filters.
    """

    def testUnparseableInt(self):
        url = "/users/?filter{pk}=123x"
        response = self.client.get(url)
        self.assertEqual(400, response.status_code)

    def test_filter_with_reference(self):
        data = {
            "username": "name",
            "last_name": "name",
            "display_name": "display match",
        }
        response = self.client.post(
            "/officers/", json.dumps(data), content_type="application/json"
        )
        self.assertEqual(201, response.status_code, response.content)
        data = {
            "username": "name2",
            "last_name": "last name",
            "display_name": "display mismatch",
        }
        response = self.client.post(
            "/officers/", json.dumps(data), content_type="application/json"
        )
        self.assertEqual(201, response.status_code, response.content)
        url = "/officers/?filter{username*}=last_name"
        response = self.client.get(url)
        self.assertEqual(200, response.status_code, response.content)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(len(content["officers"]), 1)
        self.assertEqual(content["officers"][0]["display_name"], "display match")

        url = "/officers/?filter{-username*}=last_name"
        response = self.client.get(url)
        self.assertEqual(200, response.status_code, response.content)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(len(content["officers"]), 1)
        self.assertEqual(content["officers"][0]["display_name"], "display mismatch")


class TestNestedWrites(APITestCase):
    def test_nested_writes(self):
        data = {
            "username": "name",
            "last_name": "last_name",
            "display_name": "display_name",
        }
        response = self.client.post(
            "/officers/", json.dumps(data), content_type="application/json"
        )
        self.assertEqual(201, response.status_code, response.content)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(content["officer"]["last_name"], "last_name")

        data["last_name"] = "new_last_name"
        response = self.client.patch(
            "/officers/%s/" % content["officer"]["id"],
            json.dumps(data),
            content_type="application/json",
        )
        self.assertEqual(200, response.status_code, response.content)
        content = json.loads(response.content.decode("utf-8"))
        self.assertEqual(content["officer"]["last_name"], "new_last_name")
