//! What a save game is about, read straight from the file: its name, the
//! household being played, and the pictures the game stored with it.
//!
//! A save is a DBPF archive. Three kinds of entry matter here:
//!
//! - `0x0D`: the save's data, a protobuf message, usually RefPack-compressed;
//! - `0x14`: a JPEG portrait of the played household;
//! - `0x0F`: JPEG pictures of lots, keyed by zone id.
//!
//! Only the handful of fields shown to the player are read. Everything else is
//! skipped, so a newer game adding fields does not break this.

use std::fs;
use std::path::Path;

use crate::{Error, Result, io_err};

const SAVE_DATA: u32 = 0x0D;
const LOT_PICTURE: u32 = 0x0F;
const LOT_PICTURE_PACKED: u32 = 0x10;
const HOUSEHOLD_PORTRAIT: u32 = 0x14;

const COMPRESSION_NONE: u16 = 0;
const COMPRESSION_ZLIB: u16 = 0x5A42;
const COMPRESSION_REFPACK: u16 = 0xFFFF;

/// Nothing in a save comes near this; a bigger claim is a damaged file.
const MAX_PLAIN_SIZE: usize = 512 << 20;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SavePreview {
    /// The name the player gave the save in the game.
    pub name: Option<String>,
    /// The household being played.
    pub household: Option<String>,
    /// Its members, as "first last".
    pub sims: Vec<String>,
    pub funds: Option<u64>,
    /// The lot the household lives on.
    pub lot: Option<String>,
    /// JPEG of the household.
    pub portrait: Option<Vec<u8>>,
    /// JPEG of the home lot.
    pub lot_picture: Option<Vec<u8>>,
}

pub fn read_preview(path: &Path) -> Result<SavePreview> {
    let data = fs::read(path).map_err(io_err(path))?;
    parse_preview(&data).map_err(|reason| Error::Save {
        path: path.to_path_buf(),
        reason,
    })
}

type Parsed<T> = std::result::Result<T, &'static str>;

fn parse_preview(data: &[u8]) -> Parsed<SavePreview> {
    let entries = index(data)?;
    let mut preview = SavePreview::default();
    let mut home_zone = None;
    if let Some(entry) = entries.iter().find(|entry| entry.kind == SAVE_DATA) {
        home_zone = read_save_data(&entry.body(data)?, &mut preview)?;
    }
    let picture = |kinds: &[u32], instance: Option<u64>| {
        kinds.iter().find_map(|&kind| {
            let entry = entries
                .iter()
                .find(|entry| entry.kind == kind && instance.is_none_or(|wanted| entry.instance == wanted))?;
            entry.body(data).ok().filter(|body| body.starts_with(&[0xFF, 0xD8]))
        })
    };
    preview.portrait = picture(&[HOUSEHOLD_PORTRAIT], None);
    if let Some(zone) = home_zone.filter(|&zone| zone != 0) {
        preview.lot_picture = picture(&[LOT_PICTURE, LOT_PICTURE_PACKED], Some(zone));
    }
    Ok(preview)
}

struct Entry {
    kind: u32,
    instance: u64,
    offset: usize,
    size: usize,
    plain_size: usize,
    compression: u16,
}

impl Entry {
    fn body(&self, data: &[u8]) -> Parsed<Vec<u8>> {
        let end = self.offset.checked_add(self.size).ok_or("entry out of range")?;
        let raw = data.get(self.offset..end).ok_or("entry out of range")?;
        match self.compression {
            COMPRESSION_NONE => Ok(raw.to_vec()),
            COMPRESSION_REFPACK => refpack(raw),
            COMPRESSION_ZLIB => {
                let limit = self.plain_size.min(MAX_PLAIN_SIZE);
                miniz_oxide::inflate::decompress_to_vec_zlib_with_limit(raw, limit).map_err(|_| "bad zlib data")
            }
            _ => Err("unknown compression"),
        }
    }
}

struct Reader<'a> {
    data: &'a [u8],
    at: usize,
}

impl<'a> Reader<'a> {
    fn new(data: &'a [u8], at: usize) -> Self {
        Self { data, at }
    }

    fn done(&self) -> bool {
        self.at >= self.data.len()
    }

    fn take(&mut self, count: usize) -> Parsed<&'a [u8]> {
        let end = self.at.checked_add(count).ok_or("unexpected end")?;
        let bytes = self.data.get(self.at..end).ok_or("unexpected end")?;
        self.at = end;
        Ok(bytes)
    }

    fn u8(&mut self) -> Parsed<u8> {
        Ok(self.take(1)?[0])
    }

    fn u16(&mut self) -> Parsed<u16> {
        Ok(u16::from_le_bytes(self.take(2)?.try_into().unwrap()))
    }

    fn u32(&mut self) -> Parsed<u32> {
        Ok(u32::from_le_bytes(self.take(4)?.try_into().unwrap()))
    }

    fn u64(&mut self) -> Parsed<u64> {
        Ok(u64::from_le_bytes(self.take(8)?.try_into().unwrap()))
    }

    fn varint(&mut self) -> Parsed<u64> {
        let mut value = 0u64;
        for shift in (0..64).step_by(7) {
            let byte = self.u8()?;
            value |= u64::from(byte & 0x7F) << shift;
            if byte < 0x80 {
                return Ok(value);
            }
        }
        Err("bad varint")
    }
}

/// The archive's table of contents.
fn index(data: &[u8]) -> Parsed<Vec<Entry>> {
    if !data.starts_with(b"DBPF") {
        return Err("not a save file");
    }
    let count = Reader::new(data, 36).u32()? as usize;
    let mut position = Reader::new(data, 64).u64()?;
    if position == 0 {
        position = u64::from(Reader::new(data, 40).u32()?);
    }
    let mut reader = Reader::new(data, usize::try_from(position).map_err(|_| "index out of range")?);
    // The flags say which of the first three fields are the same for every
    // entry and so are stored once, ahead of the entries.
    let flags = reader.u32()?;
    let mut shared = [None; 3];
    for (bit, slot) in shared.iter_mut().enumerate() {
        if flags & (1 << bit) != 0 {
            *slot = Some(reader.u32()?);
        }
    }
    let mut entries = Vec::with_capacity(count.min(4096));
    for _ in 0..count {
        let mut own = [0u32; 3];
        for (slot, shared) in own.iter_mut().zip(shared) {
            *slot = match shared {
                Some(value) => value,
                None => reader.u32()?,
            };
        }
        let [kind, _group, instance_high] = own;
        let instance_low = reader.u32()?;
        let offset = reader.u32()? as usize;
        let size = reader.u32()?;
        let plain_size = reader.u32()? as usize;
        let compression = if size & 0x8000_0000 != 0 {
            let compression = reader.u16()?;
            reader.u16()?;
            compression
        } else {
            COMPRESSION_NONE
        };
        entries.push(Entry {
            kind,
            instance: u64::from(instance_high) << 32 | u64::from(instance_low),
            offset,
            size: (size & 0x7FFF_FFFF) as usize,
            plain_size,
            compression,
        });
    }
    Ok(entries)
}

/// Undoes RefPack, the compression older EA formats use: runs of plain bytes
/// mixed with copies of bytes already written.
fn refpack(data: &[u8]) -> Parsed<Vec<u8>> {
    let mut reader = Reader::new(data, 0);
    let flags = reader.u8()?;
    reader.u8()?;
    let wide = flags & 0x80 != 0;
    let width = if wide { 4 } else { 3 };
    if flags & 0x01 != 0 {
        // The compressed size, which we already know.
        reader.take(width)?;
    }
    let size = reader.take(width)?.iter().fold(0usize, |size, &byte| size << 8 | usize::from(byte));
    if size > MAX_PLAIN_SIZE {
        return Err("save data too large");
    }
    let mut out = Vec::with_capacity(size);
    loop {
        let first = usize::from(reader.u8()?);
        let (plain, copy, offset) = match first {
            0x00..0x80 => {
                let second = usize::from(reader.u8()?);
                (first & 3, ((first & 0x1C) >> 2) + 3, ((first & 0x60) << 3) + second + 1)
            }
            0x80..0xC0 => {
                let second = usize::from(reader.u8()?);
                let third = usize::from(reader.u8()?);
                (second >> 6, (first & 0x3F) + 4, ((second & 0x3F) << 8) + third + 1)
            }
            0xC0..0xE0 => {
                let second = usize::from(reader.u8()?);
                let third = usize::from(reader.u8()?);
                let fourth = usize::from(reader.u8()?);
                (
                    first & 3,
                    ((first & 0x0C) << 6) + fourth + 5,
                    ((first & 0x10) << 12) + (second << 8) + third + 1,
                )
            }
            0xE0..0xFC => (((first & 0x1F) << 2) + 4, 0, 0),
            _ => (first & 3, 0, 0),
        };
        out.extend_from_slice(reader.take(plain)?);
        if copy > 0 {
            if offset > out.len() {
                return Err("bad compressed data");
            }
            // The copy may overlap what it writes, so it goes byte by byte.
            for _ in 0..copy {
                out.push(out[out.len() - offset]);
            }
        }
        if out.len() > size {
            return Err("bad compressed data");
        }
        if first >= 0xFC {
            break;
        }
    }
    if out.len() != size {
        return Err("bad compressed data");
    }
    Ok(out)
}

enum Value<'a> {
    Number(u64),
    Bytes(&'a [u8]),
}

impl Value<'_> {
    fn number(&self) -> Option<u64> {
        match self {
            Value::Number(number) => Some(*number),
            Value::Bytes(_) => None,
        }
    }

    fn text(&self) -> Option<String> {
        match self {
            Value::Bytes(bytes) => {
                let text = String::from_utf8_lossy(bytes).trim().to_string();
                (!text.is_empty()).then_some(text)
            }
            Value::Number(_) => None,
        }
    }
}

/// Calls `visit` with each field of a protobuf message.
fn fields<'a>(message: &'a [u8], mut visit: impl FnMut(u64, Value<'a>)) -> Parsed<()> {
    let mut reader = Reader::new(message, 0);
    while !reader.done() {
        let key = reader.varint()?;
        let value = match key & 7 {
            0 => Value::Number(reader.varint()?),
            1 => Value::Number(reader.u64()?),
            2 => {
                let length = usize::try_from(reader.varint()?).map_err(|_| "bad field length")?;
                Value::Bytes(reader.take(length)?)
            }
            5 => Value::Number(u64::from(reader.u32()?)),
            _ => return Err("bad save data"),
        };
        visit(key >> 3, value);
    }
    Ok(())
}

/// Fills in the names and returns the played household's home zone.
fn read_save_data(message: &[u8], preview: &mut SavePreview) -> Parsed<Option<u64>> {
    let (mut slot, mut households, mut sims, mut zones) = (None, Vec::new(), Vec::new(), Vec::new());
    fields(message, |field, value| {
        if let Value::Bytes(bytes) = value {
            match field {
                2 => slot = Some(bytes),
                5 => households.push(bytes),
                6 => sims.push(bytes),
                7 => zones.push(bytes),
                _ => {}
            }
        }
    })?;

    let mut active = None;
    if let Some(slot) = slot {
        fields(slot, |field, value| match field {
            9 => preview.name = value.text(),
            11 => active = value.number().filter(|&id| id != 0),
            _ => {}
        })?;
    }

    #[derive(Default)]
    struct Household {
        id: u64,
        name: Option<String>,
        home_zone: u64,
        funds: Option<u64>,
        played: bool,
    }
    let mut found: Option<Household> = None;
    for bytes in households {
        let mut household = Household::default();
        fields(bytes, |field, value| match field {
            2 => household.id = value.number().unwrap_or(0),
            3 => household.name = value.text(),
            4 => household.home_zone = value.number().unwrap_or(0),
            5 => household.funds = value.number(),
            31 => household.played = value.number() == Some(1),
            _ => {}
        })?;
        // The save names its household; an older one may only mark it.
        let wanted = match active {
            Some(id) => household.id == id,
            None => household.played,
        };
        if wanted {
            found = Some(household);
            break;
        }
    }
    let Some(household) = found else {
        return Ok(None);
    };

    for bytes in sims {
        let (mut owner, mut first, mut last) = (0, None, None);
        fields(bytes, |field, value| match field {
            4 => owner = value.number().unwrap_or(0),
            5 => first = value.text(),
            6 => last = value.text(),
            _ => {}
        })?;
        if owner == household.id {
            let name = [first, last].into_iter().flatten().collect::<Vec<_>>().join(" ");
            if !name.is_empty() {
                preview.sims.push(name);
            }
        }
    }
    for bytes in zones {
        let (mut id, mut name) = (0, None);
        fields(bytes, |field, value| match field {
            1 => id = value.number().unwrap_or(0),
            2 => name = value.text(),
            _ => {}
        })?;
        if id == household.home_zone && id != 0 {
            preview.lot = name;
            break;
        }
    }
    preview.household = household.name;
    preview.funds = household.funds;
    Ok(Some(household.home_zone))
}

#[cfg(test)]
mod tests {
    use super::*;

    const JPEG: &[u8] = &[0xFF, 0xD8, 0xFF, 0xE0, 1, 2, 3];

    fn varint(mut value: u64, out: &mut Vec<u8>) {
        while value >= 0x80 {
            out.push(value as u8 | 0x80);
            value >>= 7;
        }
        out.push(value as u8);
    }

    #[derive(Default)]
    struct Message(Vec<u8>);

    impl Message {
        fn number(mut self, field: u64, value: u64) -> Self {
            varint(field << 3, &mut self.0);
            varint(value, &mut self.0);
            self
        }

        fn fixed(mut self, field: u64, value: u64) -> Self {
            varint(field << 3 | 1, &mut self.0);
            self.0.extend(value.to_le_bytes());
            self
        }

        fn bytes(mut self, field: u64, value: &[u8]) -> Self {
            varint(field << 3 | 2, &mut self.0);
            varint(value.len() as u64, &mut self.0);
            self.0.extend(value);
            self
        }

        fn text(self, field: u64, value: &str) -> Self {
            self.bytes(field, value.as_bytes())
        }
    }

    /// RefPack with no back-references: valid, just not smaller.
    fn pack(plain: &[u8]) -> Vec<u8> {
        let size = plain.len();
        let mut out = vec![0x10, 0xFB, (size >> 16) as u8, (size >> 8) as u8, size as u8];
        let mut rest = plain;
        while rest.len() >= 4 {
            let run = (rest.len() / 4 * 4).min(112);
            out.push(0xE0 + (run / 4 - 1) as u8);
            out.extend(&rest[..run]);
            rest = &rest[run..];
        }
        out.push(0xFC + rest.len() as u8);
        out.extend(rest);
        out
    }

    /// A save archive holding `(type, instance, compression, stored bytes, plain size)`.
    fn archive(entries: &[(u32, u64, u16, Vec<u8>, usize)]) -> Vec<u8> {
        let mut data = vec![0u8; 96];
        data[..4].copy_from_slice(b"DBPF");
        let mut index = Vec::new();
        // Every entry shares group 0, so it is stored once.
        index.extend(2u32.to_le_bytes());
        index.extend(0u32.to_le_bytes());
        for (kind, instance, compression, body, plain_size) in entries {
            index.extend(kind.to_le_bytes());
            index.extend(((instance >> 32) as u32).to_le_bytes());
            index.extend((*instance as u32).to_le_bytes());
            index.extend((data.len() as u32).to_le_bytes());
            index.extend((body.len() as u32 | 0x8000_0000).to_le_bytes());
            index.extend((*plain_size as u32).to_le_bytes());
            index.extend(compression.to_le_bytes());
            index.extend(1u16.to_le_bytes());
            data.extend(body);
        }
        let position = data.len() as u64;
        data[36..40].copy_from_slice(&(entries.len() as u32).to_le_bytes());
        data[64..72].copy_from_slice(&position.to_le_bytes());
        data.extend(index);
        data
    }

    fn save_data(active: Option<u64>) -> Vec<u8> {
        let mut slot = Message::default().text(9, "Summer house");
        if let Some(id) = active {
            slot = slot.number(11, id);
        }
        let household = |id, name: &str, zone, funds, played| {
            Message::default()
                .fixed(2, id)
                .text(3, name)
                .number(4, zone)
                .number(5, funds)
                .number(31, played)
                .0
        };
        let sim = |household, first: &str, last: &str| {
            Message::default().fixed(1, 1).fixed(4, household).text(5, first).text(6, last).0
        };
        let zone = |id, name: &str| Message::default().fixed(1, id).text(2, name).0;
        Message::default()
            .number(1, 7)
            .bytes(2, &slot.0)
            .bytes(5, &household(10, "Neighbours", 100, 55_500, 0))
            .bytes(5, &household(20, "Ours", 200, 3_940, 1))
            .bytes(6, &sim(10, "Bella", "Stone"))
            .bytes(6, &sim(20, "Ann", "Lee"))
            .bytes(6, &sim(20, "Савелий", "Дмитриев"))
            .bytes(7, &zone(100, "Hill Manor"))
            .bytes(7, &zone(200, "Sandy Cottage"))
            .0
    }

    fn save(active: Option<u64>) -> Vec<u8> {
        let plain = save_data(active);
        archive(&[
            (HOUSEHOLD_PORTRAIT, 0, COMPRESSION_NONE, JPEG.to_vec(), JPEG.len()),
            (LOT_PICTURE, 100, COMPRESSION_NONE, [JPEG, &[100]].concat(), JPEG.len() + 1),
            (LOT_PICTURE, 200, COMPRESSION_NONE, [JPEG, &[200]].concat(), JPEG.len() + 1),
            (SAVE_DATA, 0x886B_0007, COMPRESSION_REFPACK, pack(&plain), plain.len()),
        ])
    }

    #[test]
    fn reads_the_played_household_and_its_pictures() {
        let preview = parse_preview(&save(Some(20))).unwrap();
        assert_eq!(preview.name.as_deref(), Some("Summer house"));
        assert_eq!(preview.household.as_deref(), Some("Ours"));
        assert_eq!(preview.sims, ["Ann Lee", "Савелий Дмитриев"]);
        assert_eq!(preview.funds, Some(3_940));
        assert_eq!(preview.lot.as_deref(), Some("Sandy Cottage"));
        assert_eq!(preview.portrait.as_deref(), Some(JPEG));
        assert_eq!(preview.lot_picture, Some([JPEG, &[200]].concat()));
    }

    #[test]
    fn falls_back_to_the_household_marked_as_played() {
        let preview = parse_preview(&save(None)).unwrap();
        assert_eq!(preview.household.as_deref(), Some("Ours"));
    }

    #[test]
    fn reads_zlib_and_packed_pictures() {
        let plain = save_data(Some(10));
        let packed = miniz_oxide::deflate::compress_to_vec_zlib(&plain, 6);
        let lot = [JPEG, &[9; 40]].concat();
        let data = archive(&[
            (SAVE_DATA, 1, COMPRESSION_ZLIB, packed, plain.len()),
            (LOT_PICTURE_PACKED, 100, COMPRESSION_REFPACK, pack(&lot), lot.len()),
        ]);
        let preview = parse_preview(&data).unwrap();
        assert_eq!(preview.sims, ["Bella Stone"]);
        assert_eq!(preview.lot_picture, Some(lot));
        assert_eq!(preview.portrait, None);
    }

    #[test]
    fn refpack_copies_earlier_bytes() {
        // "abc", then six bytes copied from three back, overlapping itself.
        let packed = [0x10, 0xFB, 0, 0, 9, 0x0F, 0x02, b'a', b'b', b'c', 0xFC];
        assert_eq!(refpack(&packed).unwrap(), b"abcabcabc");
    }

    #[test]
    fn damaged_files_are_errors_not_panics() {
        assert!(parse_preview(b"not a save").is_err());
        let good = save(Some(20));
        for cut in [10, 70, 100, good.len() - 30, good.len() - 1] {
            let _ = parse_preview(&good[..cut]);
        }
        // A copy reaching before the start of the output.
        assert!(refpack(&[0x10, 0xFB, 0, 0, 3, 0x00, 0x05, 0xFC]).is_err());
        // Claims more bytes than it holds.
        assert!(refpack(&[0x10, 0xFB, 0, 0, 9, 0xFC]).is_err());
    }

    #[test]
    fn a_file_without_save_data_still_gives_its_picture() {
        let data = archive(&[(HOUSEHOLD_PORTRAIT, 0, COMPRESSION_NONE, JPEG.to_vec(), JPEG.len())]);
        let preview = parse_preview(&data).unwrap();
        assert_eq!(preview.portrait.as_deref(), Some(JPEG));
        assert!(preview.sims.is_empty());
    }
}
