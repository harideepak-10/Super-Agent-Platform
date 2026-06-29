from django.urls import path
from . import views

urlpatterns = [
    path("", views.audit_list, name="audit-list"),
    path("event-types/", views.audit_event_types, name="audit-event-types"),
    path("summary/", views.audit_summary, name="audit-summary"),
    path("resource/<str:resource_type>/<str:resource_id>/", views.audit_by_resource, name="audit-by-resource"),
    path("actor/<uuid:actor_id>/", views.audit_actor, name="audit-actor"),
]
