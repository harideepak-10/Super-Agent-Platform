from django.urls import path
from . import views

urlpatterns = [
    path("", views.task_list, name="task-list"),
    path("create/", views.task_create, name="task-create"),
    path("new-task-form/", views.new_task_form, name="task-new-task-form"),
    path("<uuid:pk>/", views.task_detail, name="task-detail"),
    path("<uuid:pk>/cancel/", views.task_cancel, name="task-cancel"),
    path("<uuid:pk>/steps/", views.task_steps, name="task-steps"),
    path("<uuid:pk>/result/", views.task_result, name="task-result"),
    path("<uuid:pk>/retry/", views.task_retry, name="task-retry"),
]
