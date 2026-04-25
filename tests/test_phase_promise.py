"""Tests for tuner_promised_done in vaudeville.orchestrator._phase."""

from __future__ import annotations


class TestTunerPromisedDone:
    def test_positive_match(self) -> None:
        """Exact promise tag → True."""
        from vaudeville.orchestrator._phase import tuner_promised_done

        assert tuner_promised_done("<promise>THRESHOLDS_MET</promise>") is True

    def test_negative_no_tag(self) -> None:
        """Output without promise tag → False."""
        from vaudeville.orchestrator._phase import tuner_promised_done

        assert tuner_promised_done("Tuner finished. Metrics look good.") is False

    def test_whitespace_tolerance_inside_tag(self) -> None:
        """Whitespace around THRESHOLDS_MET inside tags is tolerated."""
        from vaudeville.orchestrator._phase import tuner_promised_done

        assert tuner_promised_done("<promise>  THRESHOLDS_MET  </promise>") is True

    def test_whitespace_tolerance_newlines(self) -> None:
        """Newlines inside promise tag are tolerated."""
        from vaudeville.orchestrator._phase import tuner_promised_done

        assert tuner_promised_done("<promise>\nTHRESHOLDS_MET\n</promise>") is True

    def test_partial_tag_no_match(self) -> None:
        """Partial or malformed tag → False."""
        from vaudeville.orchestrator._phase import tuner_promised_done

        assert tuner_promised_done("<promise>THRESHOLDS_MET") is False

    def test_multiple_promises_in_output(self) -> None:
        """Multiple promise tags in one output → True (first match)."""
        from vaudeville.orchestrator._phase import tuner_promised_done

        output = "Iter 1 done.\n<promise>THRESHOLDS_MET</promise>\nIter 2 done.\n<promise>THRESHOLDS_MET</promise>"
        assert tuner_promised_done(output) is True

    def test_empty_output(self) -> None:
        from vaudeville.orchestrator._phase import tuner_promised_done

        assert tuner_promised_done("") is False

    def test_embedded_in_prose(self) -> None:
        """Promise tag embedded in surrounding text is found."""
        from vaudeville.orchestrator._phase import tuner_promised_done

        output = "Analysis complete. <promise>THRESHOLDS_MET</promise> Continuing."
        assert tuner_promised_done(output) is True
