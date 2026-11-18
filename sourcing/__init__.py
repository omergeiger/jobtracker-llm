"""
sourcing — ATS adapters, slug registry, and batch runner for JobPostTracker.

Adapters (implement JobSource ABC from job_source.py):
    job_source_adapters.GreenhouseSource
    job_source_adapters.LeverSource
    job_source_adapters.AshbySource

Registry:
    source_registry/greenhouse.yaml  — confirmed + candidate slugs for Greenhouse
    source_registry/lever.yaml       — confirmed + candidate slugs for Lever
    source_registry/ashby.yaml       — confirmed + candidate slugs for Ashby

Runner:
    python -m sourcing.run_sourcing                                # all sources
    python -m sourcing.run_sourcing --source greenhouse            # one source
    python -m sourcing.run_sourcing --source ashby --slug openai   # ad-hoc slug test

See CLAUDE.md for API notes, field mappings, and coverage assessment.
"""
