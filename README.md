# DP Tools Local Setup

This repository contains the local setup scripts and configuration for the Guardant Dev Platform (GHDP) tools.

## Structure

- `platform-cli/` - The GHDP Platform CLI tool (see [platform-cli/README.md](platform-cli/README.md) for details)
- `mac/` - macOS-specific setup scripts
- `.github/` - GitHub Actions workflows and configuration
- `.ghdp/` - GHDP internal configuration, orchestration, and memory
- `Jenkinsfile` - Jenkins pipeline definition
- `local-setup.iml` - IntelliJ IDEA project file

## Getting Started

The main entry point for the GHDP toolchain is the Platform CLI. To get started:

1. Install the Platform CLI (see [platform-cli/README.md#installation--uninstallation](platform-cli/README.md#installation--uninstallation))
2. For macOS users, review the setup scripts in the `mac/` directory
3. For Windows users, the Platform CLI includes Windows-specific installation scripts

## Documentation

- [Platform CLI Documentation](platform-cli/README.md)
- [GHDP Architecture](platform-cli/ARCHITECTURE.md)
- [AGENTS.md Guidelines](platform-cli/AGENTS.md)

## Contributing

This repository follows the GHDP development practices. Please see the contributing guidelines in the Platform CLI documentation.

## License

[License information would go here]
