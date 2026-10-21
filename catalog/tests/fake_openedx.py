"""An in-memory Open edX: just enough of Studio and the LMS APIs the CMS calls."""
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from core.openedx import OpenEdxError


class FakeOpenEdx:
    def __init__(self):
        self.courses = {}      # course_key -> {"title", "pacing_type", "team", "details", "image"}
        self.modes = {}        # course_key -> {mode_slug: {...}}
        self.enrollments = {}  # (username, course_key) -> {"mode", "is_active"}
        self.calls = []
        self.fail = set()      # step names that answer 500

    def install(self, test_case):
        """Patch core.openedx.studio/lms for the duration of a TestCase test."""
        for name in ("studio", "lms"):
            patcher = mock.patch(f"core.openedx.{name}", side_effect=getattr(self, name))
            patcher.start()
            test_case.addCleanup(patcher.stop)
        return self

    def _check(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise OpenEdxError(f"{name} failed", 500)

    def studio(self, method, path, **kwargs):
        if method == "POST" and path == "/api/v1/course_runs/":
            self._check("create")
            data = kwargs["json"]
            key = f"course-v1:{data['org']}+{data['number']}+{data['run']}"
            self.courses[key] = {**data, "details": {"start_date": None, "end_date": None, "overview": ""}, "image": None}
            return {"id": key}
        if path.startswith("/api/v1/course_runs/"):
            key = path.split("/")[4]
            if key not in self.courses:
                raise OpenEdxError("not found", 404)
            if path.endswith("/images/"):
                self._check("image")
                name, file, content_type = kwargs["files"]["card_image"]
                self.courses[key]["image"] = (name, content_type, file.read()[:4])
            elif method == "PATCH":
                self._check("team")
                self.courses[key]["team"] += [m for m in kwargs["json"]["team"] if m not in self.courses[key]["team"]]
            return {"id": key}
        if path.startswith("/api/contentstore/v1/course_details/"):
            key = path.rsplit("/", 1)[1]
            self._check(f"details-{method.lower()}")
            if method == "PUT":
                self.courses[key]["details"] = kwargs["json"]
            return dict(self.courses[key]["details"])
        raise AssertionError(f"unexpected Studio call {method} {path}")

    def lms(self, method, path, **kwargs):
        if path == "/api/enrollment/v1/enrollment":
            self._check("enroll" if kwargs["json"]["is_active"] else "unenroll")
            data = kwargs["json"]
            key = data["course_details"]["course_id"]
            mode = self.modes.get(key, {}).get("verified")
            expiration = mode and mode.get("expiration_datetime")
            if mode is None or (expiration and datetime.fromisoformat(expiration) < datetime.now(timezone.utc)):
                raise OpenEdxError("The [verified] course mode is expired or otherwise unavailable", 400)
            self.enrollments[(data["user"], key)] = {"mode": data["mode"], "is_active": data["is_active"]}
            return dict(self.enrollments[(data["user"], key)])
        if path.startswith("/api/course_modes/v1/courses/"):
            key = path.split("/")[5]
            modes = self.modes.setdefault(key, {})
            self._check(f"modes-{method.lower()}")
            if method == "GET":
                return list(modes.values())
            if method == "POST":
                modes[kwargs["json"]["mode_slug"]] = {"expiration_datetime": None,
                                                      "expiration_datetime_is_explicit": False, **kwargs["json"]}
                return None
            slug = path.rstrip("/").rsplit("/", 1)[1]
            if method == "PATCH":
                assert kwargs["headers"]["Content-Type"] == "application/merge-patch+json"
                modes[slug].update(json.loads(kwargs["data"]))
                return None
            if method == "DELETE":
                del modes[slug]
                return None
        raise AssertionError(f"unexpected LMS call {method} {path}")

    def publish(self, course_key, course_end):
        """
        What Open edX does on every Studio publish: a verified mode whose
        expiration isn't explicit expires 10 days before the course end.
        """
        mode = self.modes.get(course_key, {}).get("verified")
        if mode and not mode["expiration_datetime_is_explicit"]:
            mode["expiration_datetime"] = (course_end - timedelta(days=10)).isoformat()

    def has_verified_mode(self, course_key):
        return "verified" in self.modes.get(course_key, {})

    def enrollment(self, username, course_key):
        return self.enrollments.get((username, course_key))
