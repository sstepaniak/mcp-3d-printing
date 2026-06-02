# Example Prompts

Twenty prompts across five categories demonstrating what to ask and what a good response looks like.

---

## Category 1 — Profile Management

### 1. List available profiles

**Prompt:**
> List all my PrusaSlicer print profiles.

**Good response:**
> Here are your available print profiles:
>
> | Name | Category | Source |
> |------|----------|--------|
> | 0.20mm QUALITY @MK4 0.4 | print | config bundle |
> | 0.15mm QUALITY @MK4 0.4 | print | config bundle |
> | 0.30mm DRAFT @MK4 0.4 | print | config bundle |
> | Prusament PLA @MK4 | filament | config bundle |
> | Original Prusa MK4 0.4 nozzle | printer | config bundle |
>
> 5 profiles found across print, filament, and printer categories.

---

### 2. Explain a specific profile

**Prompt:**
> Explain what the "0.20mm QUALITY @MK4 0.4" profile does.

**Good response:**
> **Profile: 0.20mm QUALITY @MK4 0.4**
>
> **QUALITY**
> - Layer height: 0.20 mm (standard)
> - Top surface ironing: disabled
> - Gap fill: enabled
>
> **SPEED**
> - Perimeter speed: 45 mm/s
> - Outer perimeter speed: 25 mm/s
> - Infill speed: 80 mm/s
> - First layer speed: 20 mm/s
>
> **STRENGTH**
> - Infill density: 15% (rectilinear)
> - Perimeter count: 3
> - Bottom solid layers: 4 / Top solid layers: 5
>
> No unusual settings flagged.

---

### 3. Compare two profiles

**Prompt:**
> Compare "0.20mm QUALITY @MK4 0.4" and "0.30mm DRAFT @MK4 0.4" and tell me what's different.

**Good response:**
> These two profiles differ on 4 settings:
>
> | Setting | 0.20mm QUALITY | 0.30mm DRAFT |
> |---------|---------------|--------------|
> | layer_height | 0.20 | 0.30 |
> | first_layer_speed | 20 | 30 |
> | external_perimeter_speed | 25 | 40 |
> | top_solid_layers | 5 | 4 |
>
> The DRAFT profile prints about 35% faster at the cost of visible layer lines and slightly thinner top surfaces.

---

### 4. Create a tuned profile and save it

**Prompt:**
> Take my "0.20mm QUALITY @MK4 0.4" profile and make a faster version of it. Save it as "0.20mm FAST".

**Good response:**
> Here are the proposed changes to create a faster profile:
>
> | Setting | Current | Proposed | Reason |
> |---------|---------|----------|--------|
> | layer_height | 0.20 | 0.30 | 50% increase — fewer total layers, capped at 0.3 mm |
> | perimeters | 3 | 2 | One fewer wall cuts perimeter time |
> | fill_density | 15% | 10% | Reduced 5 pp to trim infill time |
> | infill_speed | 80 | 110 | Faster infill; least quality impact |
> | top_solid_layers | 5 | 4 | One fewer finishing layer |
>
> Profile saved as `prusa/profiles/0.20mm FAST.ini` and committed to git.

---

## Category 2 — Slicing and Diagnostics

### 5. Slice a file with the default profile

**Prompt:**
> Slice `/Users/me/models/bracket.stl` using my "0.20mm QUALITY @MK4 0.4" profile.

**Good response:**
> Slicing complete.
>
> - **Print time:** 1 h 47 m
> - **Filament used:** 3.2 g (8.7 m)
> - **Estimated cost:** $0.06
> - **Layer count:** 215
> - **Output:** `/Users/me/models/bracket.gcode`

---

### 6. Run diagnostics on a sliced file

**Prompt:**
> Run diagnostics on `/Users/me/models/bracket.gcode` and tell me if there are any potential print issues.

**Good response:**
> Diagnostics for `bracket.gcode`:
>
> - Max overhang angle: 48°
> - Bridge sections: 2
> - Support volume: 0 mm³
> - Thin wall warnings: none
> - Overhang layers: 12
>
> **No critical issues found.** Overhangs at 48° are within the printable range for PLA without supports. The two bridge sections are short enough to print cleanly at the current bridge speed.

---

### 7. Get fix suggestions for a problematic slice

**Prompt:**
> I sliced `/Users/me/models/tower.gcode` and see warnings. Suggest fixes.

**Good response:**
> Analysis of `tower.gcode` found 3 issues requiring attention:
>
> 1. **Bridge flow ratio** → set to `0.90` (currently `1.0`)
>    Multiple bridge spans detected. Reducing flow prevents sagging on unsupported areas.
>
> 2. **Bridge speed** → set to `20 mm/s` (currently `40 mm/s`)
>    Slower bridging gives filament time to cool and stiffen mid-span.
>
> 3. **Support material** → enable (currently off)
>    Overhangs reach 58° — above the printable threshold. Supports are needed to prevent drooping.
>
> Apply these changes by asking me to tune your profile with "fix bridging" and "fix overhangs".

---

### 8. Slice with custom overrides

**Prompt:**
> Slice `part.stl` using "0.20mm QUALITY @MK4 0.4" but override the infill density to 30% and disable ironing.

**Good response:**
> Slicing with overrides applied:
>
> ```
> fill_density = 30%   (was 15%)
> ironing = 0          (was 0 — already off)
> ```
>
> - **Print time:** 2 h 04 m (vs 1 h 47 m without overrides)
> - **Filament used:** 4.1 g
> - **Output:** `part.gcode`
>
> The extra 11% infill adds 17 minutes and ~0.9 g of filament.

---

## Category 3 — Design Modification in Fusion 360

### 9. List components in the active design

**Prompt:**
> What components are in my active Fusion 360 design?

**Good response:**
> Active design: **Wall Bracket v3**
>
> Components (4):
> 1. `Base Plate` — 1 occurrence
> 2. `Vertical Arm` — 1 occurrence
> 3. `Gusset` — 2 occurrences
> 4. `Mounting Boss` — 4 occurrences
>
> The design has 3 sketches, 11 features, and 2 bodies.

---

### 10. Change a parameter value

**Prompt:**
> In my active Fusion design, change the "wall_thickness" parameter to 3.5 mm.

**Good response:**
> Parameter updated:
>
> - **Parameter:** `wall_thickness`
> - **Previous value:** 2.5 mm
> - **New value:** 3.5 mm
>
> The model has been regenerated. 3 dependent features updated successfully. No errors or warnings.

---

### 11. Export the active body as STL

**Prompt:**
> Export the "Bracket" component from my active Fusion design as an STL to my Desktop.

**Good response:**
> Export complete:
>
> - **Component:** `Bracket`
> - **Output:** `/Users/me/Desktop/Bracket.stl`
> - **File size:** 48 KB
> - **Triangle count:** 2,840
> - **Refinement:** medium (default)
>
> The file is ready to slice.

---

### 12. Query the model's bounding box

**Prompt:**
> What are the overall dimensions of the active Fusion design?

**Good response:**
> Bounding box for **Wall Bracket v3**:
>
> - **X (width):** 85.0 mm
> - **Y (depth):** 42.0 mm
> - **Z (height):** 63.5 mm
>
> The part fits on a standard MK4 bed (250 × 210 mm) with room to spare. No orientation optimisation is needed.

---

## Category 4 — End-to-End Pipeline

### 13. Design to print in one step (preview only)

**Prompt:**
> Export the "Bracket" component from Fusion, slice it with my quality profile, and show me the results — but don't start printing yet.

**Good response:**
> Pipeline complete (print not started):
>
> 1. **Export** → `/tmp/Bracket.stl` (48 KB)
> 2. **Profile selected** → `0.20mm QUALITY @MK4 0.4`
> 3. **Slice results:**
>    - Print time: 1 h 52 m
>    - Filament: 3.4 g
>    - Layers: 228
> 4. **Diagnostics:** No issues found
>
> Ready to print. Run the same prompt with "start print" to send to the printer.

---

### 14. Design to print and start automatically

**Prompt:**
> Export the active Fusion component, slice it with the default PLA profile, and start the print on OctoPrint if there are no warnings.

**Good response:**
> Pipeline complete — print started:
>
> 1. **Export** → `/tmp/Component.stl`
> 2. **Sliced** with `0.20mm QUALITY @MK4 0.4`
> 3. **Diagnostics:** No warnings found
> 4. **Sent to OctoPrint** → Job ID `Component.gcode`
>    Upload URL: `http://octopi.local/api/files/local/Component.gcode`
>    Status: **printing**

---

### 15. Tune, reslice, and compare

**Prompt:**
> My last print had a lot of stringing. Take "0.20mm QUALITY @MK4 0.4", tune it to fix stringing, reslice `bracket.stl`, and show me the before/after comparison.

**Good response:**
> Tuned profile **0.20mm QUALITY @MK4 0.4 - fix stringing** saved.
>
> Changes applied:
> - `retract_length`: 0.8 → 1.3 mm
> - `retract_speed`: 40 → 50 mm/s
> - `travel_speed`: 150 → 180 mm/s
>
> Before / after slice comparison:
>
> | Metric | Original | Tuned |
> |--------|----------|-------|
> | Print time | 1 h 52 m | 1 h 54 m |
> | Filament | 3.4 g | 3.4 g |
> | Layer count | 228 | 228 |
>
> Stringing fixes have negligible impact on print time. The tuned profile is saved and ready to use.

---

### 16. Full pipeline with failure guard

**Prompt:**
> Export "HandleAssembly" from Fusion, slice with "0.30mm DRAFT", run diagnostics, and only start the print if there are zero issues.

**Good response:**
> Pipeline evaluation:
>
> 1. **Export** → `/tmp/HandleAssembly.stl`
> 2. **Sliced** — 52 min, 5.1 g
> 3. **Diagnostics found 1 issue:**
>    - Overhangs reach 62° — supports should be enabled (`support_material = 1`)
>
> **Print NOT started.** Resolve the overhang issue first (either orient the model or enable supports), then re-run the pipeline.

---

## Category 5 — Troubleshooting Print Failures

### 17. Diagnose stringing

**Prompt:**
> My prints are coming out with lots of stringy hairs between towers. What settings should I change?

**Good response:**
> Stringing is caused by molten filament oozing during travel moves. Recommended fixes:
>
> | Setting | Change | Why |
> |---------|--------|-----|
> | `retract_length` | increase by 0.5 mm (try 1.3 mm) | Pulls melt further back from the nozzle |
> | `retract_speed` | increase to 50 mm/s | Faster pull-back shortens the ooze window |
> | `travel_speed` | increase to 180 mm/s | Less time between retract and unretract |
> | `retract_before_travel` | set to 0.5 mm | Forces retraction on all moves >0.5 mm |
>
> Run "tune my profile to fix stringing" and I'll generate the updated profile for you.

---

### 18. Diagnose warping

**Prompt:**
> The corners of my PETG prints keep lifting off the bed. How do I fix this?

**Good response:**
> Corner lift is a classic warping failure caused by thermal contraction. For PETG, try:
>
> | Setting | Recommended value | Why |
> |---------|------------------|-----|
> | `brim_type` | `outer_only` | Anchors perimeters against the bed |
> | `brim_width` | 8 mm | Enough contact area to resist corner pull |
> | `first_layer_speed` | ≤ 20 mm/s | Maximises adhesion contact time |
> | `first_layer_bed_temperature` | 85–90 °C (PETG) | Hotter bed softens the bottom layer, improving bond |
> | `elefant_foot_compensation` | 0.1 mm | Prevents brim from fusing into part perimeter |
>
> Ask me to "tune my profile to fix warping" to apply these changes automatically.

---

### 19. Diagnose under-extrusion

**Prompt:**
> My prints have gaps between perimeters and the infill isn't connecting. What's wrong?

**Good response:**
> The symptoms — gaps between walls and disconnected infill — indicate under-extrusion. Common causes and fixes:
>
> **Quick software fixes:**
> - Increase `extrusion_multiplier` to 1.05 (start here; re-calibrate with a flow rate test)
> - Reduce `perimeter_speed` by 20 mm/s — slower speed gives the hotend time to melt enough material
> - Reduce `infill_speed` to prevent the nozzle from outrunning the melt zone
>
> **Hardware checks to do first:**
> 1. Check for a partial clog — cold-pull the nozzle
> 2. Verify the extruder gear isn't skipping (listen for clicking)
> 3. Confirm the correct filament diameter is set (1.75 mm)
>
> If it's software-only, ask me to "tune my profile to fix under-extrusion".

---

### 20. Diagnose layer separation

**Prompt:**
> Layers are splitting apart on my tall prints. The bottom third looks fine but the top falls apart.

**Good response:**
> Layer separation in tall prints usually means the upper layers are cooling too fast or printing too quickly to bond properly. Likely causes:
>
> 1. **Perimeter speed too high** — upper layers spend less time in contact with the nozzle and cool before bonding
> 2. **Layer height too large** — reduces the surface area ratio between layers
> 3. **Cooling too aggressive** — strong part-cooling fan prevents layers from bonding on tall narrow sections
>
> Recommended settings to try:
>
> | Setting | Change |
> |---------|--------|
> | `perimeter_speed` | reduce by 15 mm/s |
> | `external_perimeter_speed` | reduce by 10 mm/s |
> | `layer_height` | reduce 20% (e.g. 0.20 → 0.16 mm) |
> | `max_fan_speed` | reduce to 50% for the first 10 mm height |
>
> Ask me to "tune my profile to fix layer separation" and I'll build a corrected profile.
