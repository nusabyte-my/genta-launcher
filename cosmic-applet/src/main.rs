//! Genta clock applet for the COSMIC panel.
//!
//! Shows `HH:MM · Sun 15 Nov · 31° ☁` and, while a Genta focus session runs, `● 24:12`.
//! Weather and focus come from Genta's status file ($XDG_RUNTIME_DIR/genta-status.json);
//! the clock itself is local, so the applet still works when Genta is not running.
//! Left click opens the Genta launcher, middle click the Genta workspace.

use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use chrono::Local;
use cosmic::app::{Core, Task};
use cosmic::iced::{self, Subscription};
use cosmic::widget;
use cosmic::Element;
use serde::Deserialize;

const ID: &str = "dev.genta.CosmicAppletGenta";

#[derive(Debug, Default, Deserialize, Clone, PartialEq)]
struct Weather {
    temp: Option<f64>,
    symbol: Option<String>,
}

#[derive(Debug, Default, Deserialize, Clone, PartialEq)]
struct Solat {
    name: Option<String>,
    time: Option<String>,
}

#[derive(Debug, Default, Deserialize, Clone, PartialEq)]
struct BellStatus {
    tip: Option<String>,
    thinking: Option<bool>,
    meeting_soon: Option<String>,
}

#[derive(Debug, Default, Deserialize, Clone, PartialEq)]
struct Status {
    bell: Option<BellStatus>,
    solat: Option<Solat>,
    mode: Option<String>,
    text: Option<String>,
    pid: Option<u32>,
    weather: Option<Weather>,
    tips: Option<u32>,
}

fn status_path() -> PathBuf {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    runtime.join("genta-status.json")
}

fn read_status() -> Option<Status> {
    let raw = std::fs::read_to_string(status_path()).ok()?;
    let status: Status = serde_json::from_str(&raw).ok()?;
    // Ignore a stale file left behind by a Genta process that is gone.
    let alive = status
        .pid
        .map(|pid| PathBuf::from(format!("/proc/{pid}")).exists())
        .unwrap_or(false);
    alive.then_some(status)
}

fn genta_command(argument: &str) {
    genta_command_with(&[argument]);
}

fn genta_command_with(arguments: &[&str]) {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    let client = home.join("launcher/bin/genta-launcher");
    let _ = Command::new(client).args(arguments).spawn();
}

struct Applet {
    core: Core,
    label: String,
    /// Colour of the bell-state dot (same meaning as the desktop bell); None = calm, no dot.
    dot: Option<cosmic::iced::Color>,
    query: String,
}

#[derive(Debug, Clone)]
enum Message {
    Tick,
    Open,
    Workspace,
    /// Click or focus on the search bar: open Genta's search right under the bar.
    SearchFocus,
    /// Typing in the bar hands the text to Genta, which takes over the keyboard.
    SearchInput(String),
}

impl Applet {
    fn bell_colour() -> Option<cosmic::iced::Color> {
        use cosmic::iced::Color;
        let status = read_status()?;
        let bell = status.bell.clone().unwrap_or_default();
        let soon_prayer = status.solat.as_ref().and_then(|s| s.time.clone()).and_then(|time| {
            chrono::NaiveTime::parse_from_str(&time, "%H:%M").ok()
        }).map(|t| (0..=10).contains(&(t - Local::now().time()).num_minutes())).unwrap_or(false);
        if bell.thinking.unwrap_or(false) {
            Some(Color::from_rgb8(0x3E, 0xC7, 0xC2))
        } else if status.mode.as_deref() == Some("running") {
            Some(Color::from_rgb8(0xD7, 0x19, 0x21))
        } else if bell.meeting_soon.as_deref().map(|m| !m.is_empty()).unwrap_or(false) {
            Some(Color::from_rgb8(0xF0, 0xB4, 0x29))
        } else if soon_prayer {
            Some(Color::from_rgb8(0x5E, 0xDB, 0x8C))
        } else if bell.tip.as_deref().map(|t| !t.is_empty()).unwrap_or(false) {
            Some(Color::from_rgb8(0xF0, 0xB4, 0x29))
        } else {
            None
        }
    }

    fn compose() -> String {
        let now = Local::now();
        let mut parts = vec![now.format("%H:%M").to_string(), now.format("%a %-d %b").to_string()];
        if let Some(status) = read_status() {
            if let Some(weather) = status.weather {
                if let Some(temp) = weather.temp {
                    let symbol = weather.symbol.unwrap_or_default();
                    parts.push(format!("{:.0}° {}", temp, symbol).trim().to_string());
                }
            }
            // Next prayer, shown when it is within 90 minutes.
            if let Some(solat) = status.solat {
                if let (Some(name), Some(time)) = (solat.name, solat.time) {
                    let soon = chrono::NaiveTime::parse_from_str(&time, "%H:%M")
                        .map(|t| {
                            let minutes = (t - now.time()).num_minutes();
                            (0..=90).contains(&minutes)
                        })
                        .unwrap_or(false);
                    if soon {
                        parts.push(format!("☾ {name} {time}"));
                    }
                }
            }
            match status.mode.as_deref() {
                Some("running") => parts.push(format!("● {}", status.text.unwrap_or_default())),
                Some("paused") => parts.push(format!("❚❚ {}", status.text.unwrap_or_default())),
                _ => {}
            }
            let _ = status.tips;
        }
        parts.join("  ·  ")
    }
}

impl cosmic::Application for Applet {
    type Executor = cosmic::SingleThreadExecutor;
    type Flags = ();
    type Message = Message;
    const APP_ID: &'static str = ID;

    fn core(&self) -> &Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut Core {
        &mut self.core
    }

    fn init(core: Core, _flags: ()) -> (Self, Task<Message>) {
        (Self { core, label: Self::compose(), dot: Self::bell_colour(), query: String::new() }, Task::none())
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::Tick => {
                let label = Self::compose();
                if label != self.label {
                    self.label = label;
                }
                self.dot = Self::bell_colour();
            }
            Message::Open => genta_command("launcher"),
            Message::Workspace => genta_command("toggle"),
            Message::SearchFocus => genta_command_with(&["search"]),
            Message::SearchInput(text) => {
                if !text.is_empty() {
                    genta_command_with(&["search", &text]);
                }
                self.query.clear();
            }
        }
        Task::none()
    }

    fn subscription(&self) -> Subscription<Message> {
        iced::time::every(Duration::from_secs(1)).map(|_| Message::Tick)
    }

    fn view(&self) -> Element<'_, Message> {
        // Search bar (outside the clock button so clicks reach the field).
        let search = widget::text_input::search_input("Search apps, files…", &self.query)
            .on_focus(Message::SearchFocus)
            .on_input(Message::SearchInput)
            .on_submit(|_| Message::SearchFocus)
            .width(cosmic::iced::Length::Fixed(210.0));
        let mut inner: Vec<Element<'_, Message>> = Vec::new();
        if let Some(colour) = self.dot {
            inner.push(widget::text("●").class(cosmic::theme::Text::Color(colour)).into());
        }
        inner.push(self.core.applet.text(self.label.clone()).into());
        let clock_content = widget::row::with_children(inner).spacing(6).align_y(cosmic::iced::Alignment::Center);
        let clock = widget::button::custom(clock_content)
            .class(cosmic::theme::Button::AppletIcon)
            .padding([0, self.core.applet.suggested_padding(true).0])
            .on_press(Message::Open);
        let clock = widget::mouse_area(clock).on_middle_press(Message::Workspace);
        let content = widget::row::with_children(vec![search.into(), clock.into()])
            .spacing(8)
            .align_y(cosmic::iced::Alignment::Center);
        // Autosize lets the panel grow the applet to fit the content (otherwise it stays icon-sized).
        self.core.applet.autosize_window(content).into()
    }

    fn style(&self) -> Option<cosmic::iced::theme::Style> {
        Some(cosmic::applet::style())
    }
}

fn main() -> cosmic::iced::Result {
    cosmic::applet::run::<Applet>(())
}
