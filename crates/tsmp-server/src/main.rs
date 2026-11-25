//! `tsmp-server`: run a TSMP session server.
//!
//! ```text
//! tsmp-server [--listen ADDRESS] [--max-sessions N] [--max-connections N]
//!             [--per-address N] [--ping SECONDS] [--grace SECONDS]
//! ```

use std::process::ExitCode;
use std::thread;
use std::time::Duration;

use tsmp_server::{Config, Journal, Server};

/// How often the journal says how busy the server is.
const STATS_EVERY: Duration = Duration::from_secs(3600);

const USAGE: &str = "\
Usage: tsmp-server [options]

Lets TSMP launchers that cannot reach each other directly play together:
a host lists its session here and gets an invite, a guest joins with it.

Options:
  --listen ADDRESS       where to accept launchers (default 0.0.0.0:47810,
                         IPv4 only; [::]:47810 takes IPv6 and IPv4)
  --max-sessions N       sessions listed at once (default 1000)
  --max-connections N    connections open at once (default 4000)
  --per-address N        connections from one address (default 32)
  --ping SECONDS         how often hosts are pinged (default 15)
  --grace SECONDS        how long a session waits for a host that lost its
                         connection (default 120)
  -h, --help             show this and exit
  -V, --version          show the version and exit";

enum Command {
    Run(Config),
    Print(String),
}

fn parse(args: impl IntoIterator<Item = String>) -> Result<Command, String> {
    let mut config = Config::default();
    let mut args = args.into_iter();
    while let Some(flag) = args.next() {
        if matches!(flag.as_str(), "-h" | "--help") {
            return Ok(Command::Print(USAGE.to_string()));
        }
        if matches!(flag.as_str(), "-V" | "--version") {
            return Ok(Command::Print(format!("tsmp-server {}", env!("CARGO_PKG_VERSION"))));
        }
        let value = args.next().ok_or_else(|| format!("{flag} needs a value"))?;
        let bad = |what: &str| format!("{flag}: {value:?} is not {what}");
        let count = || {
            value
                .parse::<usize>()
                .ok()
                .filter(|n| *n > 0)
                .ok_or_else(|| bad("a positive number"))
        };
        let seconds = || {
            value
                .parse::<u64>()
                .ok()
                .filter(|n| *n > 0)
                .map(Duration::from_secs)
                .ok_or_else(|| bad("a number of seconds"))
        };
        match flag.as_str() {
            "--listen" => config.listen = value.parse().map_err(|_| bad("an address with a port"))?,
            "--max-sessions" => config.max_sessions = count()?,
            "--max-connections" => config.max_connections = count()?,
            "--per-address" => config.max_connections_per_address = count()?,
            "--ping" => config.ping_interval = seconds()?,
            "--grace" => config.resume_grace = seconds()?,
            _ => return Err(format!("unknown option {flag}")),
        }
    }
    Ok(Command::Run(config))
}

fn main() -> ExitCode {
    let config = match parse(std::env::args().skip(1)) {
        Ok(Command::Run(config)) => config,
        Ok(Command::Print(text)) => {
            println!("{text}");
            return ExitCode::SUCCESS;
        }
        Err(message) => {
            eprintln!("tsmp-server: {message}\n\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    let journal = Journal::stderr();
    let server = match Server::start(Config {
        journal: journal.clone(),
        ..config
    }) {
        Ok(server) => server,
        Err(e) => {
            eprintln!("tsmp-server: cannot listen: {e}");
            return ExitCode::FAILURE;
        }
    };
    loop {
        thread::sleep(STATS_EVERY);
        journal.line(&server.stats().to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(args: &[&str]) -> Result<Config, String> {
        match parse(args.iter().map(|arg| arg.to_string()))? {
            Command::Run(config) => Ok(config),
            Command::Print(_) => Err("printed".into()),
        }
    }

    #[test]
    fn options_change_the_defaults() {
        let config = run(&[
            "--listen",
            "[::]:9000",
            "--max-sessions",
            "5",
            "--ping",
            "3",
            "--grace",
            "30",
        ])
        .unwrap();
        assert_eq!(config.listen, "[::]:9000".parse().unwrap());
        assert_eq!(config.max_sessions, 5);
        assert_eq!(config.ping_interval, Duration::from_secs(3));
        assert_eq!(config.resume_grace, Duration::from_secs(30));
        assert_eq!(
            run(&[]).unwrap().listen.port(),
            tsmp_net::rendezvous::DEFAULT_SERVER_PORT
        );
    }

    #[test]
    fn bad_options_are_explained() {
        assert!(run(&["--listen", "nowhere"]).err().unwrap().contains("--listen"));
        assert!(run(&["--ping", "0"]).err().unwrap().contains("seconds"));
        assert!(run(&["--max-sessions"]).err().unwrap().contains("needs a value"));
        assert!(run(&["--frobnicate", "1"]).err().unwrap().contains("unknown"));
        assert!(matches!(parse(["--help".to_string()]), Ok(Command::Print(_))));
    }
}
