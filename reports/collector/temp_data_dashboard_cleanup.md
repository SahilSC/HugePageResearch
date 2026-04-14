# Temp Data Dashboard Cleanup

## What I changed

I cleaned up the static dashboard under `temp_data_analysis/` so the visuals and
the narrative match the underlying Redis THP experiment data again.

The main fixes were:

1. Reworked the data extraction output in `extract_data.py`.
   - Runtime improvement is now exported both as seconds and as percent.
   - Row labels are short `Row N` labels instead of long `spare-user...` axis labels.
   - The access-count distribution now uses readable cold-to-hot buckets instead of
     a linear 50-bin histogram that collapsed almost everything into the first bar.
   - Top-key entries now include short display labels and explicit improvement fields.
2. Updated `dashboard.html`.
   - Section 2 now plots the real percent improvement field instead of seconds
     while still labeling the axis as percent.
   - Section 4 now plots the new bucketed access distribution, which makes the
     skew visible instead of looking like a single-bar chart.
   - Section 5 now uses the correct axis wiring and shows blue access-count bars
     plus green runtime-improvement points for the 30 hottest keys.
   - Chart descriptions now use color chips that match the actual plotted colors.
3. Added a small regression test in `testing/test_temp_data_dashboard_data.py`.
   - This locks in the percent/seconds math.
   - This also locks in the readable bucket layout for section 4.

## Why it works

The main dashboard failures came from two different problems:

1. A rendering/config bug.
   - Section 5 used `yAxisID` values of `x` and `x2` in a horizontal chart.
   - After switching that view to the correct axis setup, the chart renders normally.
2. A data-shape problem.
   - Section 4 used a linear histogram over a highly skewed access-count range.
   - Because 4052 of 4095 keys fell into the first wide bin, the chart looked empty
     except for one dominant bar.
   - Bucketing the data into cold-to-hot ranges preserves the real skew while still
     showing more than one visible bar.

There was also a semantic mismatch in section 2:

- The chart text said "percent improvement", but the JSON and plot used raw seconds.
- The extractor now exports both values explicitly, and the chart now uses the
  percent field that matches the dashboard copy.

## Approaches considered

### Keep the old histogram and switch to a log X axis

Pros:
- Preserves the raw histogram style.

Cons:
- Still leaves awkward bin boundaries.
- Harder to read quickly in a static dashboard.

### Keep section 5 as dual horizontal bars

Pros:
- Minimal code change.

Cons:
- The runtime metric and access-count metric live on very different scales.
- The chart remained harder to read than a bar-plus-point view.

### Chosen approach

I kept the dashboard static and lightweight, but made the data shape explicit:

- bucketed bars for access distribution,
- percent improvement for key-level benefit,
- short row and key labels on the axes,
- full detail in tooltips.

This keeps the page simple while fixing the places that were misleading or
visually broken.

## Validation

I validated the cleanup in three layers:

1. Regenerated all dashboard JSON with:
   - `.venv/bin/python temp_data_analysis/extract_data.py`
2. Ran the new regression test with:
   - `.venv/bin/python -m unittest testing.test_temp_data_dashboard_data`
3. Render-checked the live HTML dashboard with headless Chromium.
   - Section 4 and section 5 both rendered with non-empty canvases.
   - No dashboard error messages were present for those sections.
   - No page errors or console errors were reported during the browser pass.

## Tradeoffs

- The section 4 buckets are now human-chosen ranges rather than an automatic
  histogram. That improves readability, but it is less "raw" than the previous
  binning.
- Section 5 now emphasizes improvement percent instead of raw runtime because
  that is the clearer comparison against the all-broken row.
