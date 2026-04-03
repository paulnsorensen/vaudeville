from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch


class TestMLXBackend:
    def _make_stream_response(self, text: str, finish_reason: str = "stop") -> Any:
        resp = MagicMock()
        resp.text = text
        resp.finish_reason = finish_reason
        return resp

    def _make_mocks(self, output: str = "VERDICT: clean") -> tuple:
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_load = MagicMock(return_value=(mock_model, mock_tokenizer))
        response = self._make_stream_response(output)
        mock_stream_generate = MagicMock(return_value=iter([response]))
        mock_generate_step = MagicMock()
        return (
            mock_model,
            mock_tokenizer,
            mock_load,
            mock_stream_generate,
            mock_generate_step,
        )

    def _patch_mlx_modules(
        self,
        mock_load: MagicMock,
        mock_stream_generate: MagicMock,
        mock_generate_step: MagicMock,
    ) -> dict[str, MagicMock]:
        """Build sys.modules dict that covers both mlx_lm and mlx_lm.generate."""
        mock_mlx = MagicMock(load=mock_load, stream_generate=mock_stream_generate)
        mock_generate_mod = MagicMock(generate_step=mock_generate_step)
        return {"mlx_lm": mock_mlx, "mlx_lm.generate": mock_generate_mod}

    def test_classify_returns_generated_text(self) -> None:
        _, _, mock_load, mock_stream_generate, mock_generate_step = self._make_mocks(
            "VERDICT: clean"
        )
        modules = self._patch_mlx_modules(
            mock_load, mock_stream_generate, mock_generate_step
        )
        with patch.dict("sys.modules", modules):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend("test-model")
            result = backend.classify("test prompt")
        assert result == "VERDICT: clean"

    def test_apply_chat_template_with_tokenizer_method(self) -> None:
        _, mock_tokenizer, mock_load, mock_stream_generate, mock_generate_step = (
            self._make_mocks()
        )
        mock_tokenizer.apply_chat_template.return_value = "<formatted>"
        modules = self._patch_mlx_modules(
            mock_load, mock_stream_generate, mock_generate_step
        )
        with patch.dict("sys.modules", modules):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend()
            backend.classify("my prompt")
        mock_tokenizer.apply_chat_template.assert_called_once_with(
            [{"role": "user", "content": "my prompt"}],
            tokenize=False,
            add_generation_prompt=True,
        )

    def test_apply_chat_template_fallback_when_no_method(self) -> None:
        class BareTokenizer:
            pass

        mock_model = MagicMock()
        mock_load = MagicMock(return_value=(mock_model, BareTokenizer()))
        _, _, _, mock_stream_generate, mock_generate_step = self._make_mocks()
        modules = self._patch_mlx_modules(
            mock_load, mock_stream_generate, mock_generate_step
        )
        with patch.dict("sys.modules", modules):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend()
            formatted = backend._apply_chat_template("hello")
        assert formatted == "<|user|>\nhello<|end|>\n<|assistant|>\n"
