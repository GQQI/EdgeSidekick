"""HTTP route modules."""

from fastapi import FastAPI

from . import (
    auth,
    book_to_skill,
    browser,
    chat,
    files,
    git,
    plugins,
    schedules,
    sessions,
    shell_jobs,
    skills,
    system,
    workspaces,
)


def register_routes(app: FastAPI) -> None:
    app.include_router(system.router)
    app.include_router(skills.router)
    app.include_router(book_to_skill.router)
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(files.router)
    app.include_router(git.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(browser.router)
    app.include_router(shell_jobs.router)
    app.include_router(schedules.router)
    app.include_router(plugins.router)
