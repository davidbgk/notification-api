from . import ma
from . import models
from marshmallow import post_load

# TODO I think marshmallow provides a better integration and error handling.
# Would be better to replace functionality in dao with the marshmallow supported
# functionality.
# http://marshmallow.readthedocs.org/en/latest/api_reference.html


class UserSchema(ma.ModelSchema):
    class Meta:
        model = models.User
        exclude = ("updated_at", "created_at", "user_to_service")


# TODO process users list, to return a list of user.id
# Should that list be restricted by the auth parsed??
class ServiceSchema(ma.ModelSchema):
    class Meta:
        model = models.Service
        exclude = ("updated_at", "created_at")


user_schema = UserSchema()
users_schema = UserSchema(many=True)
service_schema = ServiceSchema()
services_schema = ServiceSchema(many=True)
