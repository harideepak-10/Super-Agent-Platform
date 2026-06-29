from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),

    # Authentication
    path("api/v1/auth/", include("apps.authentication.urls")),

    # Tasks
    path("api/v1/tasks/", include("apps.tasks.urls")),

    # Agents
    path("api/v1/agents/", include("apps.agents.urls")),

    # Approvals
    path("api/v1/approvals/", include("apps.approvals.urls")),

    # Integrations
    path("api/v1/integrations/", include("apps.integrations.urls")),

    # Audit
    path("api/v1/audit/", include("apps.audit.urls")),

    # Costs
    path("api/v1/costs/", include("apps.costs.urls")),

    # Team
    path("api/v1/team/", include("apps.team.urls")),

    # Notifications
    path("api/v1/notifications/", include("apps.notifications.urls")),

    # Search
    path("api/v1/search/", include("apps.search.urls")),

    # Profile (part of authentication app)
    path("api/v1/profile/", include("apps.authentication.profile_urls")),

    # Customer Memory
    path("api/v1/memory/", include("apps.memory.urls")),

    # Dashboard (mobile home screen — single call for all home data)
    path("api/v1/dashboard/", include("apps.dashboard.urls")),
]
