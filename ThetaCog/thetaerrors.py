class ThetaError(Exception):
    pass


class StreamNotFound(ThetaError):
    pass


class APIError(ThetaError):
    pass


class StreamsError(ThetaError):
    pass


class InvalidThetaCredentials(ThetaError):
    pass


class OfflineStream(ThetaError):
    pass
