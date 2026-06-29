from django.urls import path
from . import views

urlpatterns = [
    path("members/", views.member_list, name="team-members"),
    path("invite/", views.invite_member, name="team-invite"),
    path("accept-invite/", views.accept_invite, name="team-accept-invite"),
    path("members/<uuid:pk>/role/", views.update_member_role, name="team-update-role"),
    path("members/<uuid:pk>/remove/", views.remove_member, name="team-remove-member"),
]
