//! A limit on wrong guesses: an address that asks for codes that do not
//! exist too often is refused for a while, which keeps session codes short
//! without making them guessable.

use std::collections::HashMap;
use std::net::IpAddr;
use std::time::{Duration, Instant};

pub(crate) struct Throttle {
    limit: usize,
    window: Duration,
    /// When each address's current window began, and its misses in it.
    misses: HashMap<IpAddr, (Instant, usize)>,
}

impl Throttle {
    pub fn new(limit: usize, window: Duration) -> Self {
        Self {
            limit,
            window,
            misses: HashMap::new(),
        }
    }

    /// Whether `address` may try now.
    pub fn allows(&self, address: IpAddr, now: Instant) -> bool {
        match self.misses.get(&address) {
            Some((since, misses)) => now.duration_since(*since) >= self.window || *misses < self.limit,
            None => true,
        }
    }

    pub fn miss(&mut self, address: IpAddr, now: Instant) {
        let entry = self.misses.entry(address).or_insert((now, 0));
        if now.duration_since(entry.0) >= self.window {
            *entry = (now, 0);
        }
        entry.1 += 1;
    }

    /// Forget windows that are over.
    pub fn prune(&mut self, now: Instant) {
        let window = self.window;
        self.misses.retain(|_, (since, _)| now.duration_since(*since) < window);
    }
}

#[cfg(test)]
mod tests {
    use std::net::Ipv4Addr;

    use super::*;

    #[test]
    fn an_address_is_held_back_for_the_rest_of_its_window() {
        let window = Duration::from_secs(60);
        let mut throttle = Throttle::new(3, window);
        let guesser = IpAddr::from(Ipv4Addr::new(203, 0, 113, 5));
        let other = IpAddr::from(Ipv4Addr::new(203, 0, 113, 6));
        let start = Instant::now();
        for _ in 0..3 {
            assert!(throttle.allows(guesser, start));
            throttle.miss(guesser, start);
        }
        assert!(!throttle.allows(guesser, start + Duration::from_secs(59)));
        assert!(throttle.allows(other, start));

        let later = start + window;
        assert!(throttle.allows(guesser, later));
        throttle.miss(guesser, later);
        assert!(throttle.allows(guesser, later));

        throttle.prune(later + window);
        assert!(throttle.misses.is_empty());
    }
}
