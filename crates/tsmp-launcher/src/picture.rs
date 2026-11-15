//! The game's pictures of a household and of a lot, made ready to draw.
//!
//! The game stores them as JPEGs with the sims or the lot cut out: what is
//! around them is left as smeared colour, and a mask saying which pixels
//! count rides along in the file. Drawn without the mask the smear shows, so
//! the mask is applied here, and a household is set against a backdrop.

use std::io::Cursor;

use image::imageops::{self, FilterType};
use image::{DynamicImage, GrayImage, ImageFormat, Rgb, RgbImage, Rgba, RgbaImage};
use tsmp_core::preview::cutout_mask;

/// The colours a household stands against when its save has no scene of its
/// own: top and bottom of the picture.
const BACKDROP: [[u8; 3]; 2] = [[0x2b, 0x40, 0x68], [0x13, 0x1e, 0x35]];
/// The scene is pulled this far towards the page's own colour, so that the
/// household stays the brightest thing in the picture.
const DIM: [u8; 3] = [0x16, 0x22, 0x3a];
const DIM_BY: f32 = 0.35;
/// The scene is shrunk to this before it is blurred: only its colours are
/// wanted, and a small picture blurs quickly.
const SCENE_SIZE: (u32, u32) = (48, 32);
const SCENE_BLUR: f32 = 2.5;

/// A household's portrait as a PNG: the sims over the save's own scene out
/// of focus, or over a plain backdrop when there is none. `None` when the
/// portrait cannot be read or carries no mask; it is then drawn as it is.
pub fn household(portrait: &[u8], scene: Option<&[u8]>) -> Option<Vec<u8>> {
    let (sims, mask) = cutout(portrait)?;
    let (width, height) = sims.dimensions();
    let mut picture = scene
        .and_then(|scene| backdrop(scene, width, height))
        .unwrap_or_else(|| plain_backdrop(width, height));
    for ((under, over), cover) in picture.pixels_mut().zip(sims.pixels()).zip(mask.pixels()) {
        *under = mix(*under, *over, f32::from(cover.0[0]) / 255.);
    }
    png(DynamicImage::ImageRgb8(picture))
}

/// A picture with what its mask leaves out made transparent, as a PNG.
/// `None` when the picture cannot be read or carries no mask.
pub fn transparent(picture: &[u8]) -> Option<Vec<u8>> {
    let (colours, mask) = cutout(picture)?;
    let mut clear = RgbaImage::new(colours.width(), colours.height());
    for ((pixel, colour), cover) in clear.pixels_mut().zip(colours.pixels()).zip(mask.pixels()) {
        let [red, green, blue] = colour.0;
        *pixel = Rgba([red, green, blue, cover.0[0]]);
    }
    png(DynamicImage::ImageRgba8(clear))
}

/// The colours of a picture and its mask, both of the picture's size.
fn cutout(picture: &[u8]) -> Option<(RgbImage, GrayImage)> {
    let mask = image::load_from_memory_with_format(cutout_mask(picture)?, ImageFormat::Png).ok()?;
    let colours = image::load_from_memory_with_format(picture, ImageFormat::Jpeg)
        .ok()?
        .into_rgb8();
    let mut mask = mask.into_luma8();
    if mask.dimensions() != colours.dimensions() {
        mask = imageops::resize(
            &mask,
            colours.width(),
            colours.height(),
            FilterType::Triangle,
        );
    }
    Some((colours, mask))
}

/// The scene a save was made in, out of focus and dimmed, filling a picture
/// of the given size.
fn backdrop(scene: &[u8], width: u32, height: u32) -> Option<RgbImage> {
    let scene = image::load_from_memory_with_format(scene, ImageFormat::Jpeg).ok()?;
    let small = scene
        .resize_to_fill(SCENE_SIZE.0, SCENE_SIZE.1, FilterType::Triangle)
        .into_rgb8();
    let soft = imageops::blur(&small, SCENE_BLUR);
    let mut wide = imageops::resize(&soft, width, height, FilterType::CatmullRom);
    for pixel in wide.pixels_mut() {
        *pixel = mix(*pixel, Rgb(DIM), DIM_BY);
    }
    Some(wide)
}

fn plain_backdrop(width: u32, height: u32) -> RgbImage {
    let [top, bottom] = BACKDROP;
    RgbImage::from_fn(width, height, |_, y| {
        mix(
            Rgb(top),
            Rgb(bottom),
            y as f32 / height.max(2).saturating_sub(1) as f32,
        )
    })
}

/// `under` with `over` laid on it at the given strength, from 0 to 1.
fn mix(under: Rgb<u8>, over: Rgb<u8>, strength: f32) -> Rgb<u8> {
    let channel = |index: usize| {
        let (under, over) = (f32::from(under.0[index]), f32::from(over.0[index]));
        (under + (over - under) * strength).round() as u8
    };
    Rgb([channel(0), channel(1), channel(2)])
}

fn png(picture: DynamicImage) -> Option<Vec<u8>> {
    let mut bytes = Vec::new();
    picture
        .write_to(&mut Cursor::new(&mut bytes), ImageFormat::Png)
        .ok()?;
    Some(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A JPEG of one colour carrying the given mask, the way the game writes
    /// its pictures.
    fn masked(colour: [u8; 3], mask: &GrayImage) -> Vec<u8> {
        let (width, height) = mask.dimensions();
        let mut jpeg = Vec::new();
        DynamicImage::ImageRgb8(RgbImage::from_pixel(width, height, Rgb(colour)))
            .write_to(&mut Cursor::new(&mut jpeg), ImageFormat::Jpeg)
            .unwrap();
        let mask = png(DynamicImage::ImageLuma8(mask.clone())).unwrap();
        // The mask goes in a segment of its own, right after the first one.
        let first = 4 + usize::from(u16::from_be_bytes([jpeg[4], jpeg[5]]));
        let mut segment = vec![0xFF, 0xE0];
        segment.extend(((mask.len() + 10) as u16).to_be_bytes());
        segment.extend(b"ALFA");
        segment.extend((mask.len() as u32).to_be_bytes());
        segment.extend(mask);
        jpeg.splice(first..first, segment);
        jpeg
    }

    fn half_mask() -> GrayImage {
        GrayImage::from_fn(16, 16, |x, _| image::Luma([if x < 8 { 255 } else { 0 }]))
    }

    fn close(one: [u8; 3], other: [u8; 3]) -> bool {
        one.iter()
            .zip(other)
            .all(|(&one, other)| one.abs_diff(other) < 12)
    }

    #[test]
    fn keeps_the_sims_and_replaces_what_is_around_them() {
        let red = [200, 30, 30];
        let picture = household(&masked(red, &half_mask()), None).unwrap();
        let picture = image::load_from_memory(&picture).unwrap().into_rgb8();
        assert!(close(picture.get_pixel(2, 8).0, red));
        assert!(!close(picture.get_pixel(13, 8).0, red));
        assert!(close(picture.get_pixel(13, 0).0, BACKDROP[0]));
    }

    #[test]
    fn makes_the_rest_of_a_lot_transparent() {
        let picture = transparent(&masked([20, 160, 60], &half_mask())).unwrap();
        let picture = image::load_from_memory(&picture).unwrap().into_rgba8();
        assert_eq!(picture.get_pixel(2, 8).0[3], 255);
        assert_eq!(picture.get_pixel(13, 8).0[3], 0);
    }

    #[test]
    fn leaves_a_picture_without_a_mask_alone() {
        let mut plain = Vec::new();
        DynamicImage::ImageRgb8(RgbImage::new(8, 8))
            .write_to(&mut Cursor::new(&mut plain), ImageFormat::Jpeg)
            .unwrap();
        assert_eq!(household(&plain, None), None);
        assert_eq!(transparent(&plain), None);
    }
}
