"""Space-only dependency injection; this module creates no external clients."""


class BadRequestError(Exception):
    pass


class ChatCompletionModel:
    def __new__(cls, cfg):
        return cfg.space_model
