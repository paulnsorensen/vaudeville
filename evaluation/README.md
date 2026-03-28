# Evaluation

Rule evaluation benchmarks and accuracy testing for Vaudeville classifiers.

This directory contains datasets and tooling for measuring detection accuracy
of YAML rule definitions against labeled examples.

## Structure

- Labeled transcript samples go here as YAML files
- Run `uv run python -m vaudeville.eval` to score rules against samples
