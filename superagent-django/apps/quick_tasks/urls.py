from django.urls import path
from . import views

urlpatterns = [
    path("",        views.quick_task_list,   name="quick-task-list"),
    path("remove/", views.quick_task_remove, name="quick-task-remove"),
]
