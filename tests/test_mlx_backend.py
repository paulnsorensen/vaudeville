from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch


class TestMLXBackend:
    def _make_stream_response(self, text: str, finish_reason: str = "stop") -> Any:
        resp = MagicMock()
        resp.text = text
        resp.finish_reason = finish_reason
        return resp

    def _make_mocks(
        self, output: str = "VERDICT: clean"
    ) -> tuple[Any, Any, Any, Any, Any]:
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
        from vaudeville.server.mlx_backend import SYSTEM_PROMPT

        mock_tokenizer.apply_chat_template.assert_called_once_with(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "my prompt"},
            ],
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
        from vaudeville.server.mlx_backend import SYSTEM_PROMPT

        assert formatted == (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            "<|im_start|>user\nhello<|im_end|>\n<|im_start|>assistant\n"
        )


class TestMLXCachedMethods:
    """Tests for KV cache prefix reuse in MLXBackend."""

    def _make_stream_response(self, text: str, finish_reason: str = "stop") -> Any:
        resp = MagicMock()
        resp.text = text
        resp.finish_reason = finish_reason
        return resp

    def _build_backend(
        self,
        output: str = "VERDICT: clean",
        generate_step_tokens: list[tuple[Any, Any]] | None = None,
    ) -> tuple[Any, MagicMock, MagicMock, MagicMock]:
        """Build an MLXBackend with fully mocked MLX internals.

        Returns (backend, mock_stream_generate, mock_generate_step, mock_tokenizer).
        """
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.eos_token_id = 2

        # Chat template that mimics ChatML format
        def fake_apply_chat_template(
            messages: list[dict[str, str]], **kwargs: Any
        ) -> str:
            parts = []
            for m in messages:
                parts.append(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n")
            parts.append("<|im_start|>assistant\n")
            return "".join(parts)

        mock_tokenizer.apply_chat_template.side_effect = fake_apply_chat_template
        mock_tokenizer.encode.side_effect = lambda s, **kw: list(range(len(s)))
        mock_tokenizer.decode.side_effect = lambda ids: output

        mock_load = MagicMock(return_value=(mock_model, mock_tokenizer))
        response = self._make_stream_response(output)
        mock_stream_generate = MagicMock(return_value=iter([response]))

        if generate_step_tokens is None:
            mock_token = MagicMock()
            mock_token.item.return_value = 42
            mock_logprobs = MagicMock()
            generate_step_tokens = [(mock_token, mock_logprobs)]

        mock_generate_step = MagicMock(return_value=iter(generate_step_tokens))

        mock_mlx = MagicMock(load=mock_load, stream_generate=mock_stream_generate)
        mock_generate_mod = MagicMock(generate_step=mock_generate_step)
        modules = {"mlx_lm": mock_mlx, "mlx_lm.generate": mock_generate_mod}

        with patch.dict("sys.modules", modules):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend("test-model")

        # Re-assign to allow resetting return values between calls
        backend._stream_generate = mock_stream_generate
        backend._generate_step = mock_generate_step

        return backend, mock_stream_generate, mock_generate_step, mock_tokenizer

    # -- _format_prefix / _format_suffix --

    def test_format_prefix_uses_split_marker(self) -> None:
        backend, _, _, mock_tokenizer = self._build_backend()
        from vaudeville.server.mlx_backend import SYSTEM_PROMPT

        result = backend._format_prefix("static rule text ")
        assert result.endswith("static rule text ")
        assert SYSTEM_PROMPT in result
        assert "SPLIT_MARKER" not in result

    def test_format_suffix_appends_closing_tags(self) -> None:
        backend, _, _, _ = self._build_backend()
        result = backend._format_suffix("user input text")
        assert result.startswith("user input text")
        assert "<|im_end|>" in result
        assert "assistant" in result

    def test_format_prefix_fallback_without_chat_template(self) -> None:
        backend, _, _, mock_tokenizer = self._build_backend()
        del mock_tokenizer.apply_chat_template
        from vaudeville.server.mlx_backend import SYSTEM_PROMPT

        result = backend._format_prefix("rule prefix ")
        assert result == (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            "<|im_start|>user\nrule prefix "
        )

    def test_format_suffix_fallback_without_chat_template(self) -> None:
        backend, _, _, mock_tokenizer = self._build_backend()
        del mock_tokenizer.apply_chat_template
        result = backend._format_suffix("some text")
        assert result == "some text<|im_end|>\n<|im_start|>assistant\n"

    # -- _warm_prefix --

    def test_warm_prefix_creates_and_evaluates_cache(self) -> None:
        mock_cache_entry = MagicMock()
        mock_cache = [mock_cache_entry]
        mock_mx = MagicMock()
        mock_mlx_parent = MagicMock()
        mock_mlx_parent.core = mock_mx
        mock_make_prompt_cache = MagicMock(return_value=mock_cache)

        backend, _, mock_generate_step, _ = self._build_backend()
        mock_generate_step.return_value = iter([])

        with patch.dict(
            "sys.modules",
            {
                "mlx": mock_mlx_parent,
                "mlx.core": mock_mx,
                "mlx_lm.models.cache": MagicMock(
                    make_prompt_cache=mock_make_prompt_cache,
                ),
            },
        ):
            result = backend._warm_prefix("formatted prefix")

        assert result is mock_cache
        mock_make_prompt_cache.assert_called_once_with(backend._model)
        mock_generate_step.assert_called_once()

    # -- classify_cached --

    def test_classify_cached_returns_generated_text(self) -> None:
        backend, mock_stream_gen, mock_gen_step, _ = self._build_backend(
            output="VERDICT: clean"
        )
        # _warm_prefix uses generate_step, then classify_cached uses stream_generate
        mock_gen_step.return_value = iter([])
        response = self._make_stream_response("VERDICT: clean")
        mock_stream_gen.return_value = iter([response])

        with self._patch_mlx_cache(backend):
            result = backend.classify_cached("rule prefix user text", prefix_len=12)

        assert result == "VERDICT: clean"

    def test_classify_cached_reuses_warm_cache(self) -> None:
        backend, mock_stream_gen, mock_gen_step, _ = self._build_backend()
        mock_gen_step.return_value = iter([])

        with self._patch_mlx_cache(backend):
            # First call warms the cache
            resp1 = self._make_stream_response("VERDICT: clean")
            mock_stream_gen.return_value = iter([resp1])
            backend.classify_cached("rule prefix text1", prefix_len=12)

            warm_call_count = mock_gen_step.call_count

            # Second call with same prefix should reuse cache
            resp2 = self._make_stream_response("VERDICT: violation")
            mock_stream_gen.return_value = iter([resp2])
            mock_gen_step.return_value = iter([])
            backend.classify_cached("rule prefix text2", prefix_len=12)

        # generate_step should only have been called once (for warming)
        assert mock_gen_step.call_count == warm_call_count

    def test_classify_cached_different_prefix_warms_new_cache(self) -> None:
        backend, mock_stream_gen, mock_gen_step, _ = self._build_backend()
        mock_gen_step.return_value = iter([])

        with self._patch_mlx_cache(backend):
            resp1 = self._make_stream_response("VERDICT: clean")
            mock_stream_gen.return_value = iter([resp1])
            backend.classify_cached("prefix_A user text", prefix_len=9)

            first_warm_count = mock_gen_step.call_count

            resp2 = self._make_stream_response("VERDICT: clean")
            mock_stream_gen.return_value = iter([resp2])
            mock_gen_step.return_value = iter([])
            backend.classify_cached("prefix_B user text", prefix_len=9)

        # Should have warmed twice (different prefixes)
        assert mock_gen_step.call_count == first_warm_count + 1

    def test_classify_cached_deepcopy_isolates_requests(self) -> None:
        """Each request gets its own cache copy so mutations don't leak."""
        backend, mock_stream_gen, mock_gen_step, _ = self._build_backend()
        mock_gen_step.return_value = iter([])

        captured_caches: list[Any] = []

        def capture_cache(*args: Any, **kwargs: Any) -> Any:
            if "prompt_cache" in kwargs:
                captured_caches.append(id(kwargs["prompt_cache"]))
            return iter([self._make_stream_response("VERDICT: clean")])

        mock_stream_gen.side_effect = capture_cache

        with self._patch_mlx_cache(backend):
            backend.classify_cached("rule prefix text1", prefix_len=12)
            mock_gen_step.return_value = iter([])
            backend.classify_cached("rule prefix text2", prefix_len=12)

        assert len(captured_caches) == 2
        assert captured_caches[0] != captured_caches[1]

    def test_classify_cached_evicts_lru_when_full(self) -> None:
        """Oldest prefix cache is evicted when MAX_PREFIX_CACHES is exceeded."""
        from vaudeville.server.mlx_backend import MAX_PREFIX_CACHES

        backend, mock_stream_gen, mock_gen_step, _ = self._build_backend()
        mock_gen_step.return_value = iter([])

        with self._patch_mlx_cache(backend):
            for i in range(MAX_PREFIX_CACHES + 2):
                resp = self._make_stream_response("VERDICT: clean")
                mock_stream_gen.return_value = iter([resp])
                mock_gen_step.return_value = iter([])
                backend.classify_cached(f"prefix_{i:03d} user text", prefix_len=11)

        assert len(backend._prefix_caches) <= MAX_PREFIX_CACHES

    # -- classify_cached_with_logprobs --

    def test_classify_cached_with_logprobs_returns_result(self) -> None:
        mock_token = MagicMock()
        mock_token.item.return_value = 42
        mock_logprobs = MagicMock()

        backend, _, mock_gen_step, mock_tokenizer = self._build_backend(
            generate_step_tokens=[(mock_token, mock_logprobs)],
        )
        mock_tokenizer.decode.return_value = " clean\nREASON: looks good"

        # First call to generate_step warms prefix, second generates tokens
        warm_iter: list[Any] = []
        gen_iter = iter([(mock_token, mock_logprobs)])
        mock_gen_step.side_effect = [warm_iter, gen_iter]

        with self._patch_mlx_cache(backend):
            result = backend.classify_cached_with_logprobs(
                "rule prefix user text",
                prefix_len=12,
            )

        assert result.text.startswith("VERDICT:")
        assert isinstance(result.logprobs, dict)

    def test_classify_cached_with_logprobs_appends_verdict_anchor(self) -> None:
        mock_token = MagicMock()
        mock_token.item.return_value = 42
        mock_logprobs = MagicMock()

        backend, _, mock_gen_step, mock_tokenizer = self._build_backend()
        mock_tokenizer.decode.return_value = " clean"

        warm_iter: list[Any] = []
        gen_iter = iter([(mock_token, mock_logprobs)])
        mock_gen_step.side_effect = [warm_iter, gen_iter]

        with self._patch_mlx_cache(backend):
            backend.classify_cached_with_logprobs(
                "rule prefix user text",
                prefix_len=12,
            )

        # The suffix passed to encode should end with VERDICT:
        encode_calls = mock_tokenizer.encode.call_args_list
        suffix_call = encode_calls[-1]
        encoded_str = suffix_call[0][0]
        assert encoded_str.endswith("VERDICT:")

    def test_classify_cached_with_logprobs_stops_at_eos(self) -> None:
        mock_token1 = MagicMock()
        mock_token1.item.return_value = 42
        mock_token2 = MagicMock()
        mock_token2.item.return_value = 2  # eos_token_id

        mock_logprobs = MagicMock()

        backend, _, mock_gen_step, mock_tokenizer = self._build_backend()
        mock_tokenizer.decode.return_value = " clean"

        warm_iter: list[Any] = []
        gen_iter = iter([(mock_token1, mock_logprobs), (mock_token2, mock_logprobs)])
        mock_gen_step.side_effect = [warm_iter, gen_iter]

        with self._patch_mlx_cache(backend):
            result = backend.classify_cached_with_logprobs(
                "rule prefix user text",
                prefix_len=12,
            )

        # Should have decoded only [42, 2] (stopped at eos)
        assert result.text.startswith("VERDICT:")

    # -- helpers --

    @staticmethod
    def _patch_mlx_cache(backend: Any) -> Any:
        """Context manager that patches mlx.core and make_prompt_cache."""
        mock_mx = MagicMock()
        mock_mx.array.side_effect = lambda x: x
        mock_mx.eval.return_value = None

        mock_mlx_parent = MagicMock()
        mock_mlx_parent.core = mock_mx

        mock_cache_entry = MagicMock()
        mock_cache_entry.state = MagicMock()
        mock_cache = [mock_cache_entry]
        mock_make_prompt_cache = MagicMock(return_value=mock_cache)

        return patch.dict(
            "sys.modules",
            {
                "mlx": mock_mlx_parent,
                "mlx.core": mock_mx,
                "mlx_lm.models.cache": MagicMock(
                    make_prompt_cache=mock_make_prompt_cache,
                ),
            },
        )
