class StreamsError(Exception):
    pass


class StreamNotFound(StreamsError):
    pass


class APIError(StreamsError):
    pass


class InvalidThetaCredentials(StreamsError):
    pass


class OfflineStream(StreamsError):
    pass
