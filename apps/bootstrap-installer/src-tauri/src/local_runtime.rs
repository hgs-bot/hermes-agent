//! Post-install contract for the lightweight `hermes local` CLI surface.
//!
//! The bootstrap installer clones the pinned repository and `uv sync` installs
//! it into `<install-root>/venv`; it does not bundle Python modules or llama.cpp
//! archives in the Tauri executable.  This probe runs after those install stages
//! so a packaging regression cannot publish the completion marker.

use anyhow::{anyhow, Context, Result};
use std::path::{Path, PathBuf};
use std::process::Command;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug)]
struct CliProbeOutput {
    success: bool,
    stdout: String,
    stderr: String,
}

fn installed_hermes_cli(install_root: &Path, windows: bool) -> PathBuf {
    if windows {
        // Build this as a string rather than Path::join so the Windows contract
        // remains testable on the Linux CI lane.
        PathBuf::from(format!(
            "{}\\venv\\Scripts\\hermes.exe",
            install_root.display()
        ))
    } else {
        install_root.join("venv").join("bin").join("hermes")
    }
}

fn verify_local_cli_with<F>(install_root: &Path, windows: bool, mut run: F) -> Result<()>
where
    F: FnMut(&Path, &[String]) -> Result<CliProbeOutput>,
{
    let cli = installed_hermes_cli(install_root, windows);
    let args = vec!["local".to_string(), "--help".to_string()];
    let output = run(&cli, &args)
        .with_context(|| format!("could not execute {} local --help", cli.display()))?;
    if !output.success {
        return Err(anyhow!(
            "installed Hermes failed `hermes local --help` ({}): {}",
            cli.display(),
            output.stderr.trim()
        ));
    }
    for required in ["pull", "start", "stop", "status"] {
        if !output.stdout.contains(required) {
            return Err(anyhow!(
                "installed `hermes local --help` is missing the {required} action"
            ));
        }
    }
    Ok(())
}

/// Verify the console script installed by `uv sync` exposes the complete local
/// runtime command. This deliberately performs no pull: llama.cpp and model
/// assets remain lazy, checksum-pinned downloads owned by the Python runtime.
pub(crate) fn verify_installed_local_cli(install_root: &Path) -> Result<()> {
    verify_local_cli_with(install_root, cfg!(target_os = "windows"), |program, args| {
        let mut command = Command::new(program);
        command.args(args);
        #[cfg(target_os = "windows")]
        command.creation_flags(CREATE_NO_WINDOW);
        let output = command.output()?;
        Ok(CliProbeOutput {
            success: output.status.success(),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    #[test]
    fn windows_install_uses_the_venv_console_script() {
        assert_eq!(
            installed_hermes_cli(Path::new(r"C:\Users\fresh\hermes-agent"), true),
            PathBuf::from(r"C:\Users\fresh\hermes-agent\venv\Scripts\hermes.exe")
        );
    }

    #[test]
    fn probe_requires_local_help_to_succeed() {
        let root = Path::new(r"C:\Users\fresh\hermes-agent");
        let mut observed = None;
        verify_local_cli_with(root, true, |program, args| {
            observed = Some((program.to_path_buf(), args.to_vec()));
            Ok(CliProbeOutput {
                success: true,
                stdout: "usage: hermes local {pull,start,stop,status}".into(),
                stderr: String::new(),
            })
        })
        .expect("the installed local CLI contract should pass");

        assert_eq!(
            observed,
            Some((
                PathBuf::from(r"C:\Users\fresh\hermes-agent\venv\Scripts\hermes.exe"),
                vec!["local".to_string(), "--help".to_string()],
            ))
        );
    }

    #[test]
    fn probe_rejects_an_install_without_the_local_subcommand() {
        let err = verify_local_cli_with(Path::new("/fresh/hermes-agent"), false, |_program, _args| {
            Ok(CliProbeOutput {
                success: false,
                stdout: String::new(),
                stderr: "invalid choice: 'local'".into(),
            })
        })
        .expect_err("an old or incompletely packaged CLI must fail installation");

        assert!(format!("{err:#}").contains("hermes local --help"));
    }
}