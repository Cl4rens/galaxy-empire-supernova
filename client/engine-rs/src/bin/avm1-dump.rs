//! Print the ActionScript 2 of a screen (a converted .swf), to see what its buttons and
//! functions do.
//!
//!     avm1-dump <screen.swf> [text to look for]
//!
//! With a text, prints only the code blocks that contain it.

use std::fmt::Write;

use anyhow::Result;
use swf::avm1::read::Reader;
use swf::avm1::types::{Action, Value};
use swf::{SwfStr, Tag, UTF_8};

fn text(s: &SwfStr) -> String {
    s.to_str_lossy(UTF_8).into_owned()
}

fn disassemble(out: &mut String, data: &[u8], version: u8, pool: &mut Vec<String>, depth: usize) {
    let pad = "  ".repeat(depth);
    let mut reader = Reader::new(data, version);
    loop {
        let position = data.len() - reader.get_mut().len();
        if reader.get_mut().is_empty() {
            break;
        }
        match reader.read_action() {
            Ok(Action::ConstantPool(cp)) => {
                *pool = cp.strings.iter().map(|s| text(s)).collect();
                let _ = writeln!(out, "{pad}{position:5} ConstantPool[{}]", pool.len());
            }
            Ok(Action::Push(push)) => {
                let values: Vec<String> = push
                    .values
                    .iter()
                    .map(|v| match v {
                        Value::ConstantPool(i) => format!("{:?}", pool.get(*i as usize).map(String::as_str).unwrap_or("?")),
                        Value::Str(s) => format!("{:?}", text(s)),
                        other => format!("{other:?}"),
                    })
                    .collect();
                let _ = writeln!(out, "{pad}{position:5} Push {}", values.join(", "));
            }
            Ok(Action::DefineFunction(f)) => {
                let params: Vec<String> = f.params.iter().map(|p| text(p)).collect();
                let _ = writeln!(out, "{pad}{position:5} function {}({})", text(f.name), params.join(", "));
                disassemble(out, f.actions, version, &mut pool.clone(), depth + 1);
            }
            Ok(Action::DefineFunction2(f)) => {
                let params: Vec<String> = f
                    .params
                    .iter()
                    .map(|p| match p.register_index {
                        Some(r) => format!("{}=r{}", text(p.name), r),
                        None => text(p.name),
                    })
                    .collect();
                let _ = writeln!(out, "{pad}{position:5} function2 {}({}) {:?}", text(f.name), params.join(", "), f.flags);
                disassemble(out, f.actions, version, &mut pool.clone(), depth + 1);
            }
            Ok(action) => {
                let _ = writeln!(out, "{pad}{position:5} {action:?}");
            }
            Err(e) => {
                let _ = writeln!(out, "{pad}{position:5} <{e:?}>");
                break;
            }
        }
    }
}

fn walk(tags: &[Tag], version: u8, sprite: u16, names: &std::collections::HashMap<u16, String>, filter: Option<&str>) {
    let mut frame = 1;
    let mut show = |title: String, data: &[u8]| {
        let mut out = String::new();
        disassemble(&mut out, data, version, &mut Vec::new(), 1);
        if filter.is_none_or(|f| out.contains(f)) {
            println!("== {title}\n{out}");
        }
    };
    let owner = |id: u16| match names.get(&id) {
        Some(name) => format!("sprite {id} ({name})"),
        None => format!("sprite {id}"),
    };
    for tag in tags {
        match tag {
            Tag::ShowFrame => frame += 1,
            Tag::DoAction(data) => show(format!("{} frame {frame}", owner(sprite)), data),
            Tag::DoInitAction { id, action_data } => show(format!("init {}", owner(*id)), action_data),
            Tag::DefineSprite(s) => walk(&s.tags, version, s.id, names, filter),
            Tag::DefineButton2(button) => {
                for action in &button.actions {
                    show(format!("button {} {:?}", button.id, action.conditions), action.action_data);
                }
            }
            Tag::PlaceObject(place) => {
                if let Some(actions) = &place.clip_actions {
                    for record in &actions.records {
                        let name = place.name.map(text).unwrap_or_default();
                        show(format!("{} clip '{name}' depth {} {:?}", owner(sprite), place.depth, record.events), record.action_data);
                    }
                }
            }
            _ => {}
        }
    }
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let data = std::fs::read(&args[1])?;
    let buf = swf::decompress_swf(&data[..])?;
    let movie = swf::parse_swf(&buf)?;
    let mut names = std::collections::HashMap::new();
    fn exports<'a>(tags: &[Tag<'a>], names: &mut std::collections::HashMap<u16, String>) {
        for tag in tags {
            match tag {
                Tag::ExportAssets(assets) => {
                    for asset in assets {
                        names.insert(asset.id, text(asset.name));
                    }
                }
                Tag::DefineSprite(s) => exports(&s.tags, names),
                _ => {}
            }
        }
    }
    exports(&movie.tags, &mut names);
    walk(&movie.tags, movie.header.version(), 0, &names, args.get(2).map(String::as_str));
    Ok(())
}
