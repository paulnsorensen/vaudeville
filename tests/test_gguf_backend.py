from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestGGUFBackend:
    def _make_mock_llama(self, content: str = "VERDICT: clean") -> MagicMock:
        mock_llm = MagicMock()
        mock_llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return mock_llm

    def test_classify_returns_string(self) -> None:
        mock_llm = self._make_mock_llama("VERDICT: violation\nREASON: test")
        mock_lm_cls = MagicMock(return_value=mock_llm)
        mock_hub = MagicMock(return_value="/tmp/fake-model.gguf")

        with patch.dict(
            "sys.modules",
            {
                "huggingface_hub": MagicMock(hf_hub_download=mock_hub),
                "llama_cpp": MagicMock(Llama=mock_lm_cls),
            },
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend()
            result = backend.classify("test prompt", max_tokens=50)

        assert result == "VERDICT: violation\nREASON: test"

    def test_classify_passes_max_tokens(self) -> None:
        mock_llm = self._make_mock_llama()
        mock_lm_cls = MagicMock(return_value=mock_llm)
        mock_hub = MagicMock(return_value="/tmp/fake.gguf")

        with patch.dict(
            "sys.modules",
            {
                "huggingface_hub": MagicMock(hf_hub_download=mock_hub),
                "llama_cpp": MagicMock(Llama=mock_lm_cls),
            },
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend()
            backend.classify("prompt", max_tokens=25)

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        assert call_kwargs["max_tokens"] == 25
