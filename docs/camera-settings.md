# RadCam Camera Settings Reference

This document lists the camera settings DORIS can read and apply on the RadCam (Blue Robotics 4K “br4kcam”). DORIS does not talk to the camera directly; it forwards each setting to the radcam-manager extension, which speaks the camera's native HTTP protocol.

Settings are grouped by the manager's three protocol groups:

| Group | Read / Write action | Model |
|-------|--------------------|-------|
| Video / Encoding | `getVencConf / setVencConf` | `VideoParameterSettings` |
| Base image | `getImageAdjustment / setImageAdjustment` | `BaseParameterSetting` |
| Advanced image | `getImageAdjustmentEx / setImageAdjustmentEx` | `AdvancedParameterSetting` |

Notes:
- The native key (shown in parentheses) is the exact JSON key the camera expects, which is what preset files store.
- Value ranges and enumerations are taken from the radcam-manager protocol definitions (br4kcam_commands).
- Enable/disable toggles use the camera's wording: Open = enabled, Close = disabled. The numeric value differs per setting (e.g. Mirror, Flip and Lens Correction use Open = 0; most other toggles use Close = 0), so check each entry rather than assuming 0 = disabled.
- Fields marked [read-only] are reported by the camera but cannot be set.
- Some settings are only honored when a related setting has a particular value; those dependencies are noted under Values.
- Not every setting is meant for end users; the full list is exposed now for experimentation and will be trimmed to a practical subset later.
- **DORIS defaults:** the settings in sections **H (Day/Night & IR-Cut)**, **I (Light / IR LED)**, **J (Aperture / Iris)** and **M (Scene Mode)** have hardware-appropriate defaults supplied by DORIS. These are *fill-if-missing* defaults, not hard overrides: DORIS fills them in only when a preset/apply doesn't specify them, and applies them as a baseline at startup / dive start when no preset is active. An advanced user can still override any of them by including the key in a preset or hand-edited JSON — the explicit value is honored. They are simply hidden from the experimentation panel because there's no dedicated control for them yet (their values are still stored). The default values are: `color_black=0` (colour/day, IR-cut filter engaged, never night mode), `led_control=2` (illuminator disabled — no camera-controlled LEDs), `auto_iris=1` (iris disabled — fixed-aperture lens), `scene_mode=0` and `sceneMode=0` (plain general video capture).

---

## A. Video & Encoding

1. **Channel** (`channel`) — Which stream to configure.  
   *Values:* 0 Main, 1 Auxiliary, 2 Third (enum)
2. **Encode Profile** (`encode_profile`) — H.264/H.265 profile; higher compresses more efficiently at the cost of decode complexity.  
   *Values:* 0 Baseline, 1 Main, 2 High (enum)
3. **Codec** (`encode_type`) — Video codec. H.265 gives roughly the same quality at about half the bitrate.  
   *Values:* 1 = H.264, 5 = H.265 (enum)
4. **Supported Resolutions** (`pixel_list`) *[read-only]* — The width x height combinations the camera supports.  
   *Values:* read-only list of {width, height} pairs (e.g. 2560x1440, 1920x1080)
5. **Picture Width** (`pic_width`) — Encoded frame width; must match a width offered in pixel_list.  
   *Values:* integer pixels from pixel_list (e.g. 2560, 1920)
6. **Picture Height** (`pic_height`) — Encoded frame height; must match a height offered in pixel_list.  
   *Values:* integer pixels from pixel_list (e.g. 1440, 1080)
7. **Rate Control Mode** (`rc_mode`) — VBR is quality-priority; CBR gives predictable bandwidth/file size.  
   *Values:* 0 VBR, 1 CBR (enum)
8. **Bitrate** (`bitrate`) — Target bitrate; higher = better quality and larger files.  
   *Values:* integer, kbps (e.g. 6144)
9. **Max Frame Rate** (`max_framerate`) *[read-only]* — Highest frame rate the camera supports at the current resolution.  
   *Values:* read-only integer fps (e.g. 25)
10. **Frame Rate** (`frame_rate`) — Encoded frames per second.  
   *Values:* integer fps, up to max_framerate (e.g. 25)
11. **GOP / I-frame Interval** (`gop`) — Frames between keyframes; lower = faster seeking/recovery, larger files.  
   *Values:* integer, frames (e.g. 50)

---

## B. Image Tone & Color

12. **Hue** (`hue`) — Color tint / shift.  
   *Values:* integer 0-255
13. **Brightness** (`brightness`) — Overall image lightness.  
   *Values:* integer 0-255
14. **Sharpness** (`sharpness`) — Edge enhancement / apparent detail.  
   *Values:* integer 0-255
15. **Contrast** (`contrast`) — Difference between light and dark areas.  
   *Values:* integer 0-255
16. **Saturation** (`saturation`) — Color intensity.  
   *Values:* integer 0-255
17. **Gamma** (`gamma`) — Midtone response / tone curve shape.  
   *Values:* integer 0-255

---

## C. Exposure

18. **Backlight Compensation** (`blc_level`) — Brightens a subject lit from behind.  
   *Values:* integer 0-255
19. **Max Exposure** (`max_exposure`) — Longest exposure the auto-exposure loop may use, as the x in T = 1/x seconds (larger x = shorter max exposure).  
   *Values:* enum: 12, 25, 30, 50, 60, 100, 200, 400, 800, 1000, 2000, 4000, 8000
20. **Auto Exposure Mode** (`auto_exposureEx`) — Auto or manual exposure.  
   *Values:* 0 Auto, 1 Manual (enum)
21. **AE Strategy** (`AE_strategy_mode`) — Metering priority in auto exposure.  
   *Values:* 0 Highlight priority, 1 Lowlight priority (enum)
22. **Manual Exposure Time** (`exposure_time`) — Fixed shutter time as the x in T = 1/x seconds; used only in manual exposure.  
   *Values:* enum: 12, 25, 30, 50, 60, 100, 200, 400, 800, 1000, 2000, 4000, 8000, 10000, 34464

---

## D. White Balance

23. **Auto White Balance** (`auto_awb`) — Auto or manual white balance.  
   *Values:* 0 Auto, 1 Manual (enum)
24. **WB Red Gain** (`awb_red`) — Manual red channel gain (manual mode).  
   *Values:* integer 0-255
25. **WB Green Gain** (`awb_green`) — Manual green channel gain (manual mode).  
   *Values:* integer 0-255
26. **WB Blue Gain** (`awb_blue`) — Manual blue channel gain (manual mode).  
   *Values:* integer 0-255
27. **WB Scene** (`awb_auto_mode`) — Auto white-balance scene preset.  
   *Values:* 0 Scene1, 1 Scene2 (enum)
28. **WB Style Red** (`awb_style_red`) — Red bias applied on top of the auto result.  
   *Values:* integer 0-255
29. **WB Style Green** (`awb_style_green`) — Green bias applied on top of the auto result.  
   *Values:* integer 0-255
30. **WB Style Blue** (`awb_style_blue`) — Blue bias applied on top of the auto result.  
   *Values:* integer 0-255
31. **One-Push Auto White Balance** (`onceAWB`) *[Advanced group]* — Runs a single white-balance convergence against the current scene, then holds it. This is what the DORIS “Auto White Balance on Lights” toggle fires once the bottom lights come on.  
   *Values:* 0 or 1 (write 1 to trigger)

---

## E. Gain / ISO

32. **Auto Gain Mode** (`auto_gain_mode`) — Auto or manual gain.  
   *Values:* 0 Auto, 1 Manual (enum)
33. **Max Auto Digital Gain** (`auto_DGain_max`) — Ceiling for digital gain in auto mode.  
   *Values:* integer 0-255
34. **Max Auto Analog Gain** (`auto_AGain_max`) — Ceiling for analog (sensor) gain in auto mode.  
   *Values:* integer 0-255
35. **Max System Gain** (`max_sys_gain`) — Overall gain ceiling.  
   *Values:* integer 0-255
36. **Manual Analog Gain Enable** (`manual_AGain_enable`) — Enable a fixed analog gain.  
   *Values:* 0 Close (disabled), 1 Open (enabled) (enum)
37. **Manual Analog Gain** (`manual_AGain`) — Fixed analog gain value.  
   *Values:* integer 0-255
38. **Manual Digital Gain Enable** (`manual_DGain_enable`) — Enable a fixed digital gain.  
   *Values:* 0 Close (disabled), 1 Open (enabled) (enum)
39. **Manual Digital Gain** (`manual_DGain`) — Fixed digital gain value.  
   *Values:* integer 0-255

---

## F. Wide Dynamic Range & Highlight

40. **WDR Level (digital)** (`wdr_level`) — Digital wide-dynamic-range strength (balances bright and dark regions).  
   *Values:* integer 0-255
41. **WDR Sensor Enable** (`wdr_sensor`) — Enable true sensor-based WDR.  
   *Values:* 0 Close (disabled), 1 Open (enabled) (enum)
42. **WDR Sensor Level** (`wdr_level_sensor`) — Strength of the sensor-based WDR.  
   *Values:* integer 0-255
43. **HLC Enable** (`hlc_enable`) — Highlight compensation; dims very bright spots (e.g. direct light sources).  
   *Values:* 0 Close (disabled), 1 Open (enabled) (enum)

---

## G. Noise Reduction & Enhancement

44. **Noise Reduction (3D)** (`noiseReduction`) — Temporal (3D) noise reduction.  
   *Values:* 0 Close (disabled), 1 Low, 2 Middle, 3 High (enum)
45. **2D NR Level** (`_2DNR_level`) — Spatial (single-frame) noise reduction strength.  
   *Values:* 0 Low, 1 Middle, 2 High (enum)
46. **Lens Correction** (`lens_correction`) — Corrects geometric lens distortion.  
   *Values:* 0 Open (enabled), 1 Close (disabled) (enum)
47. **Anti-Fog** (`antiFog`) *[Base group]* — Defog / dehaze enhancement.  
   *Values:* 0 Close (disabled), 1 Open (enabled) (enum)
48. **Frame Turbo Pro** (`frameTurbo_pro`) *[Base group]* — Frame-rate enhancement / turbo processing.  
   *Values:* 0 Close (disabled), 1 High frame rate, 2 Ultra-high frame rate (enum)

---

## H. Day / Night & IR-Cut

> **DORIS default:** `color_black=0` (colour/day; IR-cut filter engaged; never night mode) unless a preset/JSON overrides it. The remaining entries in this section only matter in auto/night modes.

49. **Color / Black Mode** (`color_black`) — Force color, or auto-switch to black-and-white at night.  
   *Values:* 0 Color, 1 Auto (enum)
50. **IR Detect Mode** (`infr_detect_mode`) — How day/night is decided.  
   *Values:* 0 VideoDetection, 1 TimeControl, 2 PhotosensitiveDetection (enum; only when color_black = 1)
51. **Sensitivity Day->Night** (`sens_day_to_night`) — Threshold for switching into night mode.  
   *Values:* integer 0-255 (only when infr_detect_mode = 0)
52. **Sensitivity Night->Day** (`sens_night_to_day`) — Threshold for switching back to day mode.  
   *Values:* integer 0-255 (only when infr_detect_mode = 0)
53. **IR Day Hour** (`infr_day_h`) — Scheduled day-start hour (TimeControl mode).  
   *Values:* integer 0-23 (only when infr_detect_mode = 1)
54. **IR Day Minute** (`infr_day_m`) — Scheduled day-start minute.  
   *Values:* integer 0-59 (only when infr_detect_mode = 1)
55. **IR Night Hour** (`infr_night_h`) — Scheduled night-start hour.  
   *Values:* integer 0-23 (only when infr_detect_mode = 1)
56. **IR Night Minute** (`infr_night_m`) — Scheduled night-start minute.  
   *Values:* integer 0-59 (only when infr_detect_mode = 1)
57. **IR-Cut Level** (`ircut_level`) — IR-cut filter level.  
   *Values:* 0 Low, 1 High (enum)
58. **LDR / Photosensitive Level** (`ldr_level`) — Ambient light (photoresistor) level.  
   *Values:* 0 Low, 1 High (enum)

---

## I. Light / IR LED Control

> **DORIS default:** `led_control=2` (illuminator disabled) unless a preset/JSON overrides it. This vehicle has no camera-controlled LEDs, so the rest of this section is normally unused.

59. **LED Control Mode** (`led_control_mode`) — How the illuminator is driven.  
   *Values:* 0 Electrical level, 1 PWM (enum)
60. **Lamp Type** (`lamp_type`) — Illuminator type.  
   *Values:* 0 Infrared, 1 White light, 2 Auto (enum)
61. **LED Enable Level** (`led_control_avail`) — Electrical-level enable for the light.  
   *Values:* 0 Low, 1 High (enum; only when led_control_mode = 0)
62. **IR Level** (`ir_level`) — Infrared illuminator brightness.  
   *Values:* integer 0-255 (only when led_control_mode = 1)
63. **LED Level** (`led_level`) — White-light brightness.  
   *Values:* integer 0-255 (only when lamp_type = 1 and led_control_mode = 1)
64. **LED / IR Control** (`led_control`) — Illuminator enable/disable/auto.  
   *Values:* 0 Auto, 1 Open (enabled), 2 Close (disabled) (enum)

---

## J. Aperture / Iris

> **DORIS default:** `auto_iris=1` (iris disabled) unless a preset/JSON overrides it. This camera has a fixed-aperture lens, so Iris Level is never reported as settable.

65. **Auto Iris** (`auto_iris`) — Aperture mode.  
   *Values:* 0 Open (auto enabled), 1 Close (disabled), 2 Manual (enum)
66. **Iris Level** (`irisLevel`) *[read-only]* — Aperture PWM duty cycle.  
   *Values:* read-only integer 0-255 (reported only when auto_iris = 2; not settable)

---

## K. Shutter & Flicker

67. **Slow Shutter** (`low_farme_rate`) — Enable slow shutter for low-light scenes (the camera spells this key low_farme_rate).  
   *Values:* 0 Close (disabled), 1 Open (enabled) (enum)
68. **Anti-Flicker** (`anti_flicker`) — Reduces flicker under artificial light.  
   *Values:* 0 Close (disabled), 1 Auto, 2 50 Hz, 3 60 Hz (enum)
69. **Power Frequency** (`power_freq`) — Video/mains standard.  
   *Values:* 0 NTSC, 1 PAL (enum)

---

## L. Orientation

70. **Rotate** (`rotate`) *[Base group]* — Image rotation.  
   *Values:* 0 = 0°, 1 = 90°, 2 = 180°, 3 = 270° (enum)
71. **Mirror** (`mirror`) *[Advanced group]* — Horizontal flip.  
   *Values:* 0 Open (enabled), 1 Close (disabled) (enum)
72. **Flip** (`flip`) *[Advanced group]* — Vertical flip.  
   *Values:* 0 Open (enabled), 1 Close (disabled) (enum)

---

## M. Scene Mode

> **DORIS default:** advanced `scene_mode=0` (IPC / general video) and base `sceneMode=0` unless a preset/JSON overrides them. DORIS is normally used only for general video capture, so the face/plate profiles are not used.

73. **Scene Mode (Base)** (`sceneMode`) *[Base group]* — Reportedly non-functional on this camera; prefer the advanced scene mode below.  
   *Values:* 0 Off, 1 FaceCapture, 2 LicensePlateCapture (enum)
74. **Scene Mode (Advanced)** (`scene_mode`) *[Advanced group]* — Capture scene profile.  
   *Values:* 0 IPC, 1 FaceCapture, 2 LicensePlateCapture (enum)

---

## N. Restore Defaults

75. **Restore Base Defaults** (`set_default`) *[Base group]* — Reset all base image settings to factory defaults.  
   *Values:* write 1 to reset
76. **Restore Advanced Defaults** (`set_default`) *[Advanced group]* — Reset all advanced image settings to factory defaults.  
   *Values:* write 1 to reset

---

## Related DORIS automation

- **Auto White Balance on Lights** (On Bottom phase) — When enabled, the dive script fires the one-push AWB (onceAWB, #31) a couple seconds after the bottom lights first turn on, so white balance is calibrated for the lit scene rather than ambient surface light.
- **Presets** — Any combination of the settings above can be saved to a named preset. The active preset is auto-applied at DORIS startup and at dive start, and presets can be downloaded/imported as JSON using these same native keys.
