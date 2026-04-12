from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestGGUFBackend:
    @pytest.fixture(autouse=True)
    def _reset_grammar_cache(self) -> None:
        """Reset module-level grammar cache between tests."""
        import vaudeville.server.gguf_backend as mod

        mod._compiled_grammar = None

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

    def test_classify_includes_system_prompt(self) -> None:
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
            backend.classify("prompt")

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        messages = call_kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "binary classifier" in messages[0]["content"]
        assert "VERDICT: violation" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": "prompt"}

    def test_classify_with_logprobs_includes_system_prompt(self) -> None:
        mock_llm = self._make_mock_llama()
        mock_llm.create_chat_completion.return_value = {
            "choices": [
                {
                    "message": {"content": " clean\nREASON: ok"},
                    "logprobs": {
                        "content": [
                            {
                                "top_logprobs": [
                                    {"token": "clean", "logprob": -0.1},
                                ]
                            }
                        ]
                    },
                }
            ]
        }
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
            backend.classify_with_logprobs("prompt")

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        messages = call_kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "binary classifier" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": "prompt"}
        assert messages[2] == {"role": "assistant", "content": "VERDICT:"}

    def test_classify_passes_repeat_penalty(self) -> None:
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
            backend.classify("prompt")

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        assert call_kwargs["repeat_penalty"] == 1.1
        assert "grammar" in call_kwargs

    def test_classify_with_logprobs_passes_repeat_penalty(self) -> None:
        mock_llm = self._make_mock_llama()
        mock_llm.create_chat_completion.return_value = {
            "choices": [
                {
                    "message": {"content": " clean\nREASON: ok"},
                    "logprobs": {
                        "content": [
                            {
                                "top_logprobs": [
                                    {"token": "clean", "logprob": -0.1},
                                ]
                            }
                        ]
                    },
                }
            ]
        }
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
            backend.classify_with_logprobs("prompt")

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        assert call_kwargs["repeat_penalty"] == 1.1

    def test_classify_passes_grammar(self) -> None:
        mock_llm = self._make_mock_llama()
        mock_lm_cls = MagicMock(return_value=mock_llm)
        mock_hub = MagicMock(return_value="/tmp/fake.gguf")
        mock_grammar = MagicMock()
        mock_grammar_cls = MagicMock(from_string=MagicMock(return_value=mock_grammar))

        with patch.dict(
            "sys.modules",
            {
                "huggingface_hub": MagicMock(hf_hub_download=mock_hub),
                "llama_cpp": MagicMock(
                    Llama=mock_lm_cls, LlamaGrammar=mock_grammar_cls
                ),
            },
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend()
            backend.classify("prompt")

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        assert call_kwargs["grammar"] is mock_grammar
        mock_grammar_cls.from_string.assert_called_once()

    def test_classify_with_logprobs_omits_grammar(self) -> None:
        """Grammar must NOT be passed to classify_with_logprobs.

        The "VERDICT:" prefill constrains output shape; adding the grammar
        would force the model to re-emit the VERDICT prefix, so the first
        generated token (and its logprobs) would be the grammar-forced
        prefix instead of the class label.
        """
        mock_llm = self._make_mock_llama()
        mock_llm.create_chat_completion.return_value = {
            "choices": [
                {
                    "message": {"content": " clean\nREASON: ok"},
                    "logprobs": {
                        "content": [
                            {
                                "top_logprobs": [
                                    {"token": "clean", "logprob": -0.1},
                                ]
                            }
                        ]
                    },
                }
            ]
        }
        mock_lm_cls = MagicMock(return_value=mock_llm)
        mock_hub = MagicMock(return_value="/tmp/fake.gguf")
        mock_grammar_cls = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "huggingface_hub": MagicMock(hf_hub_download=mock_hub),
                "llama_cpp": MagicMock(
                    Llama=mock_lm_cls, LlamaGrammar=mock_grammar_cls
                ),
            },
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend()
            backend.classify_with_logprobs("prompt")

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        assert "grammar" not in call_kwargs

    def test_grammar_is_cached_across_calls(self) -> None:
        mock_llm = self._make_mock_llama()
        mock_lm_cls = MagicMock(return_value=mock_llm)
        mock_hub = MagicMock(return_value="/tmp/fake.gguf")
        mock_grammar = MagicMock()
        mock_grammar_cls = MagicMock(from_string=MagicMock(return_value=mock_grammar))

        with patch.dict(
            "sys.modules",
            {
                "huggingface_hub": MagicMock(hf_hub_download=mock_hub),
                "llama_cpp": MagicMock(
                    Llama=mock_lm_cls, LlamaGrammar=mock_grammar_cls
                ),
            },
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend()
            backend.classify("prompt1")
            backend.classify("prompt2")

        assert mock_grammar_cls.from_string.call_count == 1
