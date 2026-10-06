"""Verbose metadata must not leak a private prompt or provider response."""

from cli.container import _VerboseLLM


async def test_verbose_does_not_print_content(capsys):
    class Model:
        async def complete(self, prompt):
            assert prompt == "secret-private-research-input"
            return "secret-private-research-output"

    result = await _VerboseLLM(Model(), True).complete("secret-private-research-input")
    assert result == "secret-private-research-output"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret-private" not in captured.err
    assert "input_chars=" in captured.err and "output_chars=" in captured.err
