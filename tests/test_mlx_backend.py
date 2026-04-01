from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestMLXBackend:
    def _make_mocks(self, output: str = "VERDICT: clean") -> tuple:
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_load = MagicMock(return_value=(mock_model, mock_tokenizer))
        mock_generate = MagicMock(return_value=output)
        return mock_model, mock_tokenizer, mock_load, mock_generate

    def test_classify_returns_generated_text(self) -> None:
        _, _, mock_load, mock_generate = self._make_mocks("VERDICT: clean")
        mock_mlx = MagicMock(load=mock_load, generate=mock_generate)
        with patch.dict("sys.modules", {"mlx_lm": mock_mlx}):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend("test-model")
            result = backend.classify("test prompt")
        assert result == "VERDICT: clean"

    def test_apply_chat_template_with_tokenizer_method(self) -> None:
        _, mock_tokenizer, mock_load, mock_generate = self._make_mocks()
        mock_tokenizer.apply_chat_template.return_value = "<formatted>"
        mock_mlx = MagicMock(load=mock_load, generate=mock_generate)
        with patch.dict("sys.modules", {"mlx_lm": mock_mlx}):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend()
            backend.classify("my prompt")
        mock_tokenizer.apply_chat_template.assert_called_once()

    def test_apply_chat_template_fallback_when_no_method(self) -> None:
        class BareTokenizer:
            pass

        mock_model = MagicMock()
        mock_load = MagicMock(return_value=(mock_model, BareTokenizer()))
        _, _, _, mock_generate = self._make_mocks()
        mock_mlx = MagicMock(load=mock_load, generate=mock_generate)
        with patch.dict("sys.modules", {"mlx_lm": mock_mlx}):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend()
            formatted = backend._apply_chat_template("hello")
        assert formatted == "<|user|>\nhello<|end|>\n<|assistant|>\n"
