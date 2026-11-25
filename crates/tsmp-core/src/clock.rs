//! Wall-clock time as people read it, in UTC, without a time zone library.

use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};

/// Seconds since 1970-01-01 UTC; 0 on a clock set before that.
pub fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_secs())
}

/// A moment as a calendar date and a time of day, in UTC.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct UtcTime {
    pub year: u64,
    pub month: u64,
    pub day: u64,
    pub hour: u64,
    pub minute: u64,
    pub second: u64,
}

impl UtcTime {
    pub fn now() -> Self {
        Self::from_unix(unix_now())
    }

    pub fn from_unix(seconds: u64) -> Self {
        let days = seconds / 86_400;
        let of_day = seconds % 86_400;
        // Days since 1970-01-01 to a calendar date (Howard Hinnant's algorithm).
        let shifted = days + 719_468;
        let era = shifted / 146_097;
        let day_of_era = shifted % 146_097;
        let year_of_era = (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
        let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
        let month_index = (5 * day_of_year + 2) / 153;
        let day = day_of_year - (153 * month_index + 2) / 5 + 1;
        let month = if month_index < 10 { month_index + 3 } else { month_index - 9 };
        Self {
            year: year_of_era + era * 400 + u64::from(month <= 2),
            month,
            day,
            hour: of_day / 3_600,
            minute: of_day % 3_600 / 60,
            second: of_day % 60,
        }
    }

    /// `2026-11-13_22-42-54`, usable as a file or folder name.
    pub fn file_stamp(&self) -> String {
        format!(
            "{:04}-{:02}-{:02}_{:02}-{:02}-{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }
}

impl fmt::Display for UtcTime {
    /// `2026-11-13 22:42:54`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unix_times_are_calendar_dates() {
        assert_eq!(UtcTime::from_unix(0).to_string(), "1970-01-01 00:00:00");
        assert_eq!(UtcTime::from_unix(951_782_400).to_string(), "2000-02-29 00:00:00");
        assert_eq!(UtcTime::from_unix(1_794_606_174).to_string(), "2026-11-13 21:42:54");
        assert_eq!(UtcTime::from_unix(1_794_606_174).file_stamp(), "2026-11-13_21-42-54");
        assert_eq!(UtcTime::from_unix(4_107_542_399).to_string(), "2100-02-28 23:59:59");
    }
}
