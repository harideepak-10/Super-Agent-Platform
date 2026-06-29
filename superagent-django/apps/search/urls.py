from django.urls import path
from . import views

urlpatterns = [
    path("", views.global_search, name="search-global"),
    path("tasks/", views.search_tasks, name="search-tasks"),
    path("agents/", views.search_agents, name="search-agents"),
]
