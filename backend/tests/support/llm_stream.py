"""Route the adapter's streaming call to a test's mocked messages.create."""


class _Stream:
    def __init__(self, create, arguments):
        self._create, self._arguments = create, arguments

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return await self._create(**self._arguments)


def stream_via_create(sdk):
    messages = sdk.return_value.messages
    # Resolve create at call time: tests assign it after patching the SDK.
    messages.stream = lambda **arguments: _Stream(messages.create, arguments)
