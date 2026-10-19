# Run inside a Python >= 3.11 environment with the dependencies of pyproject.toml.
PY ?= python
LOCAL ?= configs/local.toml
REFERENCE ?=
export PYTHONPATH := src

HFCEV = $(PY) -m hfcev --local $(LOCAL)

.PHONY: help test sample all s1 s2 s3 s4 s5 s6 s7 stats status verify-s1 verify-s2 verify-s3 report-s3 report-s4 report-s5

help:
	@echo "make test        unit + integration tests on synthetic data"
	@echo "make sample      run stages 1-7 and stats on the synthetic sample (outputs/sample)"
	@echo "make all         run s1 ... s7 and stats on the cohort configured in $(LOCAL)"
	@echo "make s1          run stage 1 on the cohort configured in $(LOCAL)"
	@echo "make s2          run stage 2 (receive logs) on all files in L0_logs"
	@echo "make s3          run stage 3 (two-clock timeline and log additions)"
	@echo "make s4 ... s7   quality, derived quantities, vehicles, release"
	@echo "make stats       figure-ready statistics from the release"
	@echo "make report-s3 [FROZEN=...] [REFERENCE=...]   aggregate stage-3 review"
	@echo "make status      devices finished per stage"
	@echo "make verify-s1 REFERENCE=path/to/reference.json   (likewise verify-s2)"

test:
	$(PY) -m unittest discover -s tests -t .

SAMPLE_HFCEV = $(PY) -m hfcev --release outputs/sample/release.toml --local configs/local.sample.toml

sample:
	mkdir -p outputs/sample
	for d in L0_raw L0_logs protocol vehicle_map.csv; do ln -sfn ../../sample_data/$$d outputs/sample/$$d; done
	cp sample_data/cohort.csv outputs/sample/cohort.csv
	sed 's/^dbc_sha256 = .*/dbc_sha256 = "'$$(cat sample_data/protocol/sample.dbc.sha256)'"/' configs/release_v1.toml > outputs/sample/release.toml
	$(SAMPLE_HFCEV) run s1
	$(SAMPLE_HFCEV) run s2
	$(SAMPLE_HFCEV) run s3
	for s in s4 s5 s6 s7 stats; do $(SAMPLE_HFCEV) run $$s || exit 1; done

all:
	for s in s1 s2 s3 s4 s5 s6 s7 stats; do $(HFCEV) run $$s || exit 1; done

s1:
	$(HFCEV) run s1

s2:
	$(HFCEV) run s2

s3:
	$(HFCEV) run s3

s4 s5 s6 s7 stats:
	$(HFCEV) run $@

status:
	$(HFCEV) status

verify-s1:
	@test -n "$(REFERENCE)" || (echo "set REFERENCE=..."; exit 2)
	$(HFCEV) verify s1 --reference $(REFERENCE)

verify-s2:
	@test -n "$(REFERENCE)" || (echo "set REFERENCE=..."; exit 2)
	$(HFCEV) verify s2 --reference $(REFERENCE)

verify-s3:
	@test -n "$(REFERENCE)" || (echo "set REFERENCE=..."; exit 2)
	$(HFCEV) verify s3 --reference $(REFERENCE)

FROZEN ?=
report-s3:
	$(HFCEV) report s3 $(if $(FROZEN),--frozen $(FROZEN)) $(if $(REFERENCE),--reference $(REFERENCE))

report-s4:
	$(HFCEV) report s4

report-s5:
	$(HFCEV) report s5

# Only read figure statistics and the allowed release metadata/small tables.
.PHONY: figures
FIGURE_SCRIPTS = tab1_fleet tab2_fields tab3_accounting fig1_pipeline fig2_coverage fig3_clocks fig4_sampling fig5_quality fig6_physics fig7_consistency fig8_usage numbers verify
figures:
	@for script in $(FIGURE_SCRIPTS); do OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 $(PY) figures/$$script.py --local $(LOCAL) $(if $(FIGURE_OUT),--output $(FIGURE_OUT)) || exit 1; done

# Package the release for repository upload (Zenodo: <= 100 files per record).
PACKAGE_OUT ?= ../work/zenodo/v1.0
.PHONY: package
package:
	$(PY) tools/package_release.py --release ../work/L7_release --stats ../work/stats/v1.0 --out $(PACKAGE_OUT)
