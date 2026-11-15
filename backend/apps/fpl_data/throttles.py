from rest_framework.throttling import SimpleRateThrottle


class _PerIPThrottle(SimpleRateThrottle):
    """Throttle keyed on the client IP for every request, authenticated or not.

    DRF's AnonRateThrottle skips authenticated users, which would let anyone
    who registers an account bypass the limit on expensive endpoints.
    """

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class LLMBurstThrottle(_PerIPThrottle):
    scope = "llm_burst"


class LLMDailyThrottle(_PerIPThrottle):
    scope = "llm_daily"
