# Bestiarium brand sources

Source SVGs of the Bestiarium brand (the griffin mark). Home Assistant shows the PNG renders shipped in each integration's `brand/` folder; these SVGs are the sources they were made from.

| File | Use |
|---|---|
| `griffin-mark.svg` | the griffin mark on its own |
| `icon-mode.svg`, `icon-devices.svg`, `icon-climate.svg`, `icon-mock.svg` | integration icons (mark + per-integration sign) |
| `logo.svg`, `dark_logo.svg` | logo for light and dark themes |

## PNGs in the integrations

Each `custom_components/<domain>/brand/` holds the files Home Assistant (2026.3+) serves for a custom integration:

| File | Size |
|---|---|
| `icon.png`, `icon@2x.png` | 256×256, 512×512 |
| `logo.png`, `logo@2x.png` | 581×128, 1162×256 |
| `dark_logo.png`, `dark_logo@2x.png` | 581×128, 1162×256 |

There is no `dark_icon.png`: the icon reads on both light and dark backgrounds, and Home Assistant falls back to `icon.png`. Home Assistant picks up a `brand/` folder when it loads the integration, so it needs a restart after the folder is added.

## Metadata

The SVGs carry no C2PA metadata. Some editors and file hosts add a `<metadata>` block and an `xmlns:c2pa` attribute when saving; remove them before committing (removing them does not change the rendering).
