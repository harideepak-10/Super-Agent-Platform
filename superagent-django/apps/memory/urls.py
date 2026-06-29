from django.urls import path
from . import views

urlpatterns = [
    path("", views.profile_list, name="memory-list"),
    path("create/", views.profile_create, name="memory-create"),
    path("lookup/", views.profile_by_email, name="memory-lookup"),
    path("<uuid:pk>/", views.profile_detail, name="memory-detail"),
    path("<uuid:pk>/interactions/", views.profile_interactions, name="memory-interactions"),
]
