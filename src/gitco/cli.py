"""GitCo CLI interface."""

import sys
import time
from typing import Any, Optional, Union

import click

from . import __version__
from .analyzer import ChangeAnalyzer
from .config import ConfigManager, create_sample_config, get_config_manager
from .exporter import (
    export_contribution_data_to_csv,
    export_discovery_results,
    export_health_data,
    export_sync_results,
)
from .git_ops import GitRepository, GitRepositoryManager
from .github_client import create_github_client
from .utils.common import (
    console,
    create_progress_bar,
    get_logger,
    handle_validation_errors,
    log_operation_failure,
    log_operation_start,
    log_operation_success,
    print_error_panel,
    print_info_panel,
    print_success_panel,
    print_warning_panel,
    set_quiet_mode,
    setup_logging,
)
from .utils.exception import ValidationError


def print_issue_recommendation(recommendation: Any, index: int) -> None:
    """Print a formatted issue recommendation."""
    from .discovery import IssueRecommendation

    if not isinstance(recommendation, IssueRecommendation):
        return

    # Create a rich panel for the recommendation
    from rich.panel import Panel
    from rich.text import Text

    # Build the content
    content: list[Union[Text, str]] = []

    # Issue title and URL
    title_text = Text(f"#{recommendation.issue.number}: {recommendation.issue.title}")
    title_text.stylize("bold blue")
    content.append(title_text)
    content.append(f"🔗 {recommendation.issue.html_url}")
    content.append("")

    # Repository info
    content.append(f"📁 Repository: {recommendation.repository.name}")
    if recommendation.repository.language:
        content.append(f"💻 Language: {recommendation.repository.language}")
    content.append("")

    # Score and difficulty with enhanced information
    score_text = f"Score: {recommendation.overall_score:.2f}"
    difficulty_text = f"Difficulty: {recommendation.difficulty_level.title()}"
    time_text = f"Time: {recommendation.estimated_time.title()}"

    # Add confidence indicator
    if recommendation.overall_score > 0.8:
        confidence_indicator = "🎯 Excellent Match"
    elif recommendation.overall_score > 0.6:
        confidence_indicator = "⭐ Good Match"
    elif recommendation.overall_score > 0.4:
        confidence_indicator = "💡 Good Opportunity"
    else:
        confidence_indicator = "🔍 Exploration"

    content.append(
        f"{confidence_indicator} | {score_text} | 🎯 {difficulty_text} | ⏱️ {time_text}"
    )
    content.append("")

    # Skill matches with enhanced details
    if recommendation.skill_matches:
        content.append("🎯 Skill Matches:")
        for match in recommendation.skill_matches:
            confidence_text = f"({match.confidence:.1%})"
            match_type_emoji = {
                "exact": "🎯",
                "partial": "📝",
                "related": "🔗",
                "language": "💻",
            }.get(match.match_type, "📌")

            match_text = f"  {match_type_emoji} {match.skill} {confidence_text} [{match.match_type}]"
            content.append(match_text)

            # Show evidence for high-confidence matches
            if match.confidence > 0.7 and match.evidence:
                evidence_text = f"    Evidence: {match.evidence[0][:60]}..."
                content.append(f"    {evidence_text}")
        content.append("")

    # Tags with categorization
    if recommendation.tags:
        # Categorize tags
        skill_tags = [
            tag
            for tag in recommendation.tags
            if tag
            in [
                "python",
                "javascript",
                "java",
                "go",
                "rust",
                "react",
                "vue",
                "angular",
                "api",
                "database",
                "testing",
                "devops",
            ]
        ]
        difficulty_tags = [
            tag
            for tag in recommendation.tags
            if tag in ["beginner", "intermediate", "advanced"]
        ]
        time_tags = [
            tag for tag in recommendation.tags if tag in ["quick", "medium", "long"]
        ]
        special_tags = [
            tag
            for tag in recommendation.tags
            if tag not in skill_tags + difficulty_tags + time_tags
        ]

        if skill_tags:
            content.append(f"💻 Skills: {', '.join(skill_tags)}")
        if difficulty_tags:
            content.append(f"🎯 Level: {', '.join(difficulty_tags)}")
        if time_tags:
            content.append(f"⏱️ Time: {', '.join(time_tags)}")
        if special_tags:
            content.append(f"🏷️ Tags: {', '.join(special_tags)}")
        content.append("")

    # Personalized insights (if available)
    if hasattr(recommendation, "personalized_insights"):
        content.append("💡 Personalized Insights:")
        for insight in recommendation.personalized_insights[:2]:  # Show top 2 insights
            content.append(f"  • {insight}")
        content.append("")

    # Create the panel with dynamic styling
    border_style = (
        "green"
        if recommendation.overall_score > 0.7
        else "yellow"
        if recommendation.overall_score > 0.4
        else "blue"
    )

    panel = Panel(
        "\n".join(str(item) for item in content),
        title=f"Recommendation #{index}",
        border_style=border_style,
    )

    console.print(panel)
    console.print()  # Add spacing


@click.group()
@click.version_option(version=__version__, prog_name="gitco")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output")
@click.option("--log-file", help="Log to file")
@click.option(
    "--detailed-log",
    is_flag=True,
    help="Use detailed log format with function names and line numbers",
)
@click.option(
    "--max-log-size",
    type=int,
    help="Maximum log file size in MB before rotation (default: 10)",
)
@click.option(
    "--log-backups", type=int, help="Number of backup log files to keep (default: 5)"
)
@click.pass_context
def main(
    ctx: click.Context,
    verbose: bool,
    quiet: bool,
    log_file: Optional[str],
    detailed_log: bool,
    max_log_size: Optional[int],
    log_backups: Optional[int],
) -> None:
    """GitCo - A simple CLI tool for intelligent OSS fork management and contribution discovery.

    GitCo transforms the tedious process of managing multiple OSS forks into an intelligent,
    context-aware workflow. It combines automated synchronization with AI-powered insights
    to help developers stay current with upstream changes and discover meaningful
    contribution opportunities.
    """
    # Ensure context object exists
    ctx.ensure_object(dict)

    # Set global options
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    ctx.obj["log_file"] = log_file
    ctx.obj["detailed_log"] = detailed_log
    ctx.obj["max_log_size"] = max_log_size
    ctx.obj["log_backups"] = log_backups

    # Set global quiet mode state
    set_quiet_mode(quiet)

    # Calculate max file size in bytes if specified
    max_file_size = None
    if max_log_size:
        max_file_size = max_log_size * 1024 * 1024  # Convert MB to bytes

    # Setup logging with enhanced options
    setup_logging(
        verbose=verbose,
        quiet=quiet,
        log_file=log_file,
        detailed=detailed_log,
        max_file_size=max_file_size,
        backup_count=log_backups,
    )

    logger = get_logger()
    logger.debug("GitCo CLI started")


@main.command()
@click.option("--force", "-f", is_flag=True, help="Overwrite existing configuration")
@click.option("--template", "-t", help="Use custom template for configuration")
@click.pass_context
def init(ctx: click.Context, force: bool, template: Optional[str]) -> None:
    """Initialize a new GitCo configuration.

    Creates a gitco-config.yml file in the current directory with default settings.
    """
    logger = get_logger()
    log_operation_start("configuration initialization", force=force, template=template)

    try:
        config_manager = ConfigManager()

        if template:
            logger.info(f"Using custom template: {template}")
            # TODO: Implement custom template loading
        else:
            # Create default configuration
            config = config_manager.create_default_config(force=force)

            # Add sample repositories if force or new file
            if force or not config_manager.config_path:
                sample_data = create_sample_config()
                config = config_manager._parse_config(sample_data)
                config_manager.save_config(config)

        log_operation_success(
            "configuration initialization", config_file=config_manager.config_path
        )

        print_success_panel(
            "Configuration initialized successfully!",
            f"Configuration file created: {config_manager.config_path}\n\n"
            "Next steps:\n"
            "1. Edit gitco-config.yml to add your repositories\n"
            "2. Set up your LLM API key\n"
            "3. Run 'gitco sync' to start managing your forks",
        )

    except FileExistsError as e:
        log_operation_failure("configuration initialization", e, force=force)
        print_error_panel(
            "Configuration file already exists",
            "Use --force to overwrite the existing configuration file.",
        )
        sys.exit(1)
    except Exception as e:
        log_operation_failure("configuration initialization", e, force=force)
        print_error_panel("Error initializing configuration", str(e))
        sys.exit(1)


@main.command()
@click.option("--repo", "-r", help="Sync specific repository")
@click.option("--analyze", "-a", is_flag=True, help="Include AI analysis of changes")
@click.option("--export", "-e", help="Export report to file")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output")
@click.option("--log", "-l", help="Log to file")
@click.option(
    "--detailed-log",
    is_flag=True,
    help="Use detailed log format with function names and line numbers",
)
@click.option(
    "--max-log-size",
    type=int,
    help="Maximum log file size in MB before rotation (default: 10)",
)
@click.option(
    "--log-backups", type=int, help="Number of backup log files to keep (default: 5)"
)
@click.option("--batch", "-b", is_flag=True, help="Process all repositories in batch")
@click.option(
    "--max-workers",
    "-w",
    type=int,
    default=4,
    help="Maximum concurrent workers for batch processing",
)
@click.pass_context
def sync(
    ctx: click.Context,
    repo: Optional[str],
    analyze: bool,
    export: Optional[str],
    quiet: bool,
    log: Optional[str],
    detailed_log: bool,
    max_log_size: Optional[int],
    log_backups: Optional[int],
    batch: bool,
    max_workers: int,
) -> None:
    """Synchronize repositories with upstream changes.

    Fetches the latest changes from upstream repositories and merges them into your forks.
    """
    # Setup logging for sync command if log file specified
    if log:
        # Calculate max file size in bytes if specified
        max_file_size = None
        if max_log_size:
            max_file_size = max_log_size * 1024 * 1024  # Convert MB to bytes

        # Setup logging with enhanced options
        setup_logging(
            verbose=ctx.obj.get("verbose", False),
            quiet=quiet,
            log_file=log,
            detailed=detailed_log,
            max_file_size=max_file_size,
            backup_count=log_backups,
        )

    logger = get_logger()
    log_operation_start(
        "repository synchronization",
        repo=repo,
        batch=batch,
        analyze=analyze,
        log_file=log,
        detailed_log=detailed_log,
    )

    try:
        # Start timing
        start_time = time.time()

        # Load configuration
        config_manager = ConfigManager()
        config = config_manager.load_config()

        # Initialize repository manager
        repo_manager = GitRepositoryManager()

        # Track sync failures
        failed = 0

        if repo:
            # Sync specific repository
            logger.info(f"Syncing specific repository: {repo}")

            # Find repository in config
            repo_config = None
            for r in config.repositories:
                if r.name == repo:
                    repo_config = {
                        "name": r.name,
                        "local_path": r.local_path,
                        "upstream": r.upstream,
                        "fork": r.fork,
                    }
                    break

            if not repo_config:
                error_msg = f"Repository '{repo}' not found in configuration"
                log_operation_failure(
                    "repository synchronization", Exception(error_msg)
                )
                print_error_panel(
                    "Repository not found",
                    f"Repository '{repo}' not found in configuration.\n"
                    "Use 'gitco init' to create a configuration or add the repository.",
                )
                sys.exit(1)

            # Sync single repository with progress indicator
            if not quiet:
                console.print(f"[blue]🔄 Syncing repository: {repo}[/blue]")

            result = repo_manager._sync_single_repository(
                repo_config["local_path"], repo_config
            )

            if result["success"]:
                log_operation_success("repository synchronization", repo=repo)
                details = ""
                if result.get("stashed_changes"):
                    details = "📦 Uncommitted changes were stashed and restored"

                # Add retry information if recovery was attempted
                if result.get("recovery_attempted"):
                    retry_count = result.get("retry_count", 0)
                    details += f"\n🔄 Sync completed after {retry_count} retry attempts"

                if result.get("stash_restore_failed"):
                    details += "\n⚠️  Warning: Failed to restore stashed changes"

                print_success_panel(f"Successfully synced repository: {repo}", details)
            else:
                error_msg = result.get("message", "Unknown error")
                retry_info = ""
                if result.get("recovery_attempted"):
                    retry_count = result.get("retry_count", 0)
                    retry_info = f" (after {retry_count} retry attempts)"

                log_operation_failure(
                    "repository synchronization", Exception(error_msg), repo=repo
                )
                print_error_panel(
                    f"Failed to sync repository '{repo}'{retry_info}", error_msg
                )
                sys.exit(1)

        else:
            # Sync all repositories
            logger.info("Syncing all repositories")

            if batch:
                # Use batch processing
                logger.info(f"Using batch processing with {max_workers} workers")

                # Convert repositories to list of dicts for batch processing
                repositories = []
                for r in config.repositories:
                    repositories.append(
                        {
                            "name": r.name,
                            "local_path": r.local_path,
                            "upstream": r.upstream,
                            "fork": r.fork,
                            "skills": r.skills,
                            "analysis_enabled": r.analysis_enabled,
                        }
                    )

                # Process repositories in batch
                results = repo_manager.batch_sync_repositories(
                    repositories=repositories,
                    max_workers=max_workers,
                    show_progress=not quiet,
                )

                # Count successful and failed operations
                successful = sum(1 for r in results if r.success)
                failed = len(results) - successful

                if failed == 0:
                    log_operation_success(
                        "batch repository synchronization", total=len(results)
                    )
                    print_success_panel(
                        f"Successfully synced all {len(results)} repositories!",
                        "All repositories are now up to date with their upstream sources.",
                    )
                else:
                    error_msg = f"{failed} repositories failed"
                    log_operation_failure(
                        "batch repository synchronization",
                        Exception(error_msg),
                        total=len(results),
                        failed=failed,
                    )
                    print_error_panel(
                        f"Sync completed with {failed} failures",
                        f"Successfully synced {successful} repositories, {failed} failed.\n"
                        "Check the output above for details on failed repositories.\n"
                        "Some operations may have been retried due to network issues.",
                    )

            else:
                # Process repositories sequentially with progress bar
                logger.info("Processing repositories sequentially")

                successful = 0
                failed = 0
                sequential_results = []  # Store individual results for export

                if not quiet:
                    with create_progress_bar(
                        "Syncing repositories", len(config.repositories)
                    ) as progress:
                        task = progress.add_task(
                            "[cyan]Syncing repositories[/cyan]",
                            total=len(config.repositories),
                        )

                        for r in config.repositories:
                            repo_config = {
                                "name": r.name,
                                "local_path": r.local_path,
                                "upstream": r.upstream,
                                "fork": r.fork,
                            }

                            progress.update(task, description=f"Syncing {r.name}...")

                            result = repo_manager._sync_single_repository(
                                r.local_path, repo_config
                            )

                            # Store result for export
                            sequential_results.append(
                                {
                                    "name": r.name,
                                    "success": result["success"],
                                    "message": result.get("message", ""),
                                    "stashed_changes": result.get(
                                        "stashed_changes", False
                                    ),
                                    "recovery_attempted": result.get(
                                        "recovery_attempted", False
                                    ),
                                    "retry_count": result.get("retry_count", 0),
                                    "stash_restore_failed": result.get(
                                        "stash_restore_failed", False
                                    ),
                                    "details": result.get("details", {}),
                                }
                            )

                            if result["success"]:
                                successful += 1
                                message = result.get("message", "Sync completed")
                                # Add retry information to progress message
                                if result.get("recovery_attempted"):
                                    retry_count = result.get("retry_count", 0)
                                    message += f" (retry: {retry_count})"
                                progress.update(
                                    task,
                                    description=f"✅ {r.name}: {message}",
                                )
                            else:
                                failed += 1
                                message = result.get("message", "Sync failed")
                                # Add retry information to progress message
                                if result.get("recovery_attempted"):
                                    retry_count = result.get("retry_count", 0)
                                    message += f" (retry: {retry_count})"
                                progress.update(
                                    task,
                                    description=f"❌ {r.name}: {message}",
                                )

                            progress.advance(task)
                else:
                    # Quiet mode - no progress bar
                    for r in config.repositories:
                        repo_config = {
                            "name": r.name,
                            "local_path": r.local_path,
                            "upstream": r.upstream,
                            "fork": r.fork,
                        }

                        result = repo_manager._sync_single_repository(
                            r.local_path, repo_config
                        )

                        # Store result for export
                        sequential_results.append(
                            {
                                "name": r.name,
                                "success": result["success"],
                                "message": result.get("message", ""),
                                "stashed_changes": result.get("stashed_changes", False),
                                "recovery_attempted": result.get(
                                    "recovery_attempted", False
                                ),
                                "retry_count": result.get("retry_count", 0),
                                "stash_restore_failed": result.get(
                                    "stash_restore_failed", False
                                ),
                                "details": result.get("details", {}),
                            }
                        )

                        if result["success"]:
                            successful += 1
                        else:
                            failed += 1

                if failed == 0:
                    log_operation_success(
                        "sequential repository synchronization",
                        total=len(config.repositories),
                    )
                    print_success_panel(
                        f"Successfully synced all {len(config.repositories)} repositories!",
                        "All repositories are now up to date with their upstream sources.",
                    )
                else:
                    error_msg = f"{failed} repositories failed"
                    log_operation_failure(
                        "sequential repository synchronization",
                        Exception(error_msg),
                        total=len(config.repositories),
                        failed=failed,
                    )
                    print_error_panel(
                        f"Sync completed with {failed} failures",
                        f"Successfully synced {successful} repositories, {failed} failed.\n"
                        "Check the output above for details on failed repositories.\n"
                        "Some operations may have been retried due to network issues.",
                    )

        # Handle analysis if requested
        if analyze:
            logger.info("AI analysis requested")

            if repo:
                # Analyze specific repository
                try:
                    repository = config_manager.get_repository(repo)
                    if repository:
                        git_repo = GitRepository(repository.local_path)
                        if git_repo.is_git_repository():
                            analyzer = ChangeAnalyzer(config)
                            analysis = analyzer.analyze_repository_changes(
                                repository=repository,
                                git_repo=git_repo,
                            )

                            if analysis:
                                analyzer.display_analysis(analysis, repository.name)
                            else:
                                print_warning_panel(
                                    "No Analysis Available",
                                    f"No changes found to analyze for repository '{repo}'.",
                                )
                        else:
                            print_error_panel(
                                "Invalid Repository",
                                f"Path '{repository.local_path}' is not a valid Git repository.",
                            )
                    else:
                        print_error_panel(
                            "Repository Not Found",
                            f"Repository '{repo}' not found in configuration.",
                        )
                except Exception as e:
                    logger.error(f"Failed to analyze repository {repo}: {e}")
                    print_error_panel(
                        "Analysis Failed",
                        f"Failed to analyze repository '{repo}': {e}",
                    )
            else:
                # Analyze all repositories that were successfully synced
                logger.info("Analyzing all successfully synced repositories")

                analyzer = ChangeAnalyzer(config)
                analyzed_count = 0

                for r in config.repositories:
                    try:
                        git_repo = GitRepository(r.local_path)
                        if git_repo.is_git_repository():
                            analysis = analyzer.analyze_repository_changes(
                                repository=r,
                                git_repo=git_repo,
                            )

                            if analysis:
                                analyzer.display_analysis(analysis, r.name)
                                analyzed_count += 1
                    except Exception as e:
                        logger.error(f"Failed to analyze repository {r.name}: {e}")

                if analyzed_count > 0:
                    print_success_panel(
                        "Analysis Completed",
                        f"Successfully analyzed {analyzed_count} repositories.",
                    )
                else:
                    print_warning_panel(
                        "No Analysis Available",
                        "No changes found to analyze in any repositories.",
                    )

        # Handle export if requested
        if export:
            logger.info(f"Export requested to: {export}")

            # Calculate total duration
            total_duration = time.time() - start_time

            # Prepare sync data for export
            sync_data: dict[str, Any] = {
                "total_repositories": len(config.repositories) if not repo else 1,
                "successful": (
                    successful if not repo else (1 if result["success"] else 0)
                ),
                "failed": failed if not repo else (0 if result["success"] else 1),
                "batch_mode": batch,
                "analysis_enabled": analyze,
                "max_workers": max_workers,
                "success_rate": (
                    (successful / len(config.repositories))
                    if not repo and len(config.repositories) > 0
                    else (1.0 if result["success"] else 0.0)
                ),
                "total_duration": total_duration,
                "errors": [],
                "warnings": [],
            }

            # Collect repository results
            if repo:
                # Single repository sync
                sync_data["single_result"] = result
                sync_data["repository_results"] = [
                    {
                        "name": repo,
                        "success": result["success"],
                        "message": result.get("message", ""),
                        "stashed_changes": result.get("stashed_changes", False),
                        "recovery_attempted": result.get("recovery_attempted", False),
                        "retry_count": result.get("retry_count", 0),
                        "stash_restore_failed": result.get(
                            "stash_restore_failed", False
                        ),
                        "details": result.get("details", {}),
                    }
                ]
            else:
                # Multiple repository sync
                sync_data["repository_results"] = []

                if batch:
                    # Batch processing results
                    for batch_result in results:
                        sync_data["repository_results"].append(
                            {
                                "name": batch_result.repository_name,
                                "success": batch_result.success,
                                "message": batch_result.message,
                                "duration": batch_result.duration,
                                "operation": batch_result.operation,
                                "details": batch_result.details,
                                "error": (
                                    str(batch_result.error)
                                    if batch_result.error
                                    else None
                                ),
                            }
                        )
                else:
                    # Sequential processing - use collected results
                    sync_data["repository_results"] = sequential_results

            # Export the sync results
            export_sync_results(sync_data, export, repo)

        # Exit with error code if there were failures
        if not repo and failed > 0:
            sys.exit(1)

    except FileNotFoundError as e:
        log_operation_failure("repository synchronization", e)
        print_error_panel(
            "Configuration file not found",
            "Run 'gitco init' to create a configuration file.",
        )
        sys.exit(1)
    except Exception as e:
        log_operation_failure("repository synchronization", e)
        print_error_panel("Error during synchronization", str(e))
        sys.exit(1)


@main.command()
@click.option("--repo", "-r", required=True, help="Repository to analyze")
@click.option("--prompt", "-p", help="Custom analysis prompt")
@click.option("--provider", help="LLM provider to use (openai, anthropic)")
@click.option("--repos", help="Analyze multiple repositories (comma-separated)")
@click.option("--export", "-e", help="Export analysis to file")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output")
@click.pass_context
def analyze(
    ctx: click.Context,
    repo: str,
    prompt: Optional[str],
    provider: Optional[str],
    repos: Optional[str],
    export: Optional[str],
    quiet: bool,
) -> None:
    """Analyze repository changes with AI.

    Generates human-readable summaries of upstream changes using AI analysis.
    """
    log_operation_start(
        "repository analysis", repo=repo, prompt=prompt, provider=provider
    )

    try:
        # Load configuration
        config_manager = ConfigManager()
        config = config_manager.load_config()

        # Get repository configuration
        repository = config_manager.get_repository(repo)
        if not repository:
            print_error_panel(
                "Repository Not Found",
                f"Repository '{repo}' not found in configuration.\n\n"
                "Available repositories:\n"
                + "\n".join([f"  • {r.name}" for r in config.repositories]),
            )
            return

        # Initialize analyzer
        analyzer = ChangeAnalyzer(config)

        # Get Git repository instance
        git_repo = GitRepository(repository.local_path)
        if not git_repo.is_git_repository():
            print_error_panel(
                "Invalid Repository",
                f"Path '{repository.local_path}' is not a valid Git repository.",
            )
            return

        # Determine LLM provider
        selected_provider = provider or config.settings.llm_provider

        # Validate provider
        valid_providers = ["openai", "anthropic"]
        if selected_provider not in valid_providers:
            print_error_panel(
                "Invalid LLM Provider",
                f"Provider '{selected_provider}' is not supported.\n\n"
                f"Supported providers: {', '.join(valid_providers)}\n\n"
                f"Current default provider: {config.settings.llm_provider}",
            )
            return

        # Display provider information
        if provider:
            print_info_panel(
                "LLM Provider",
                f"Using provider: {selected_provider}\n"
                f"(Overriding default: {config.settings.llm_provider})",
            )
        else:
            print_info_panel(
                "LLM Provider",
                f"Using default provider: {selected_provider}",
            )

        # Perform analysis with selected provider
        analysis = analyzer.analyze_repository_changes(
            repository=repository,
            git_repo=git_repo,
            custom_prompt=prompt,
            provider=selected_provider,
        )

        if analysis:
            # Display analysis results
            analyzer.display_analysis(analysis, repository.name)

            # Export if requested
            if export:
                try:
                    import json
                    from datetime import datetime

                    export_data = {
                        "repository": repository.name,
                        "analysis_date": datetime.now().isoformat(),
                        "llm_provider": selected_provider,
                        "summary": analysis.summary,
                        "breaking_changes": analysis.breaking_changes,
                        "new_features": analysis.new_features,
                        "bug_fixes": analysis.bug_fixes,
                        "security_updates": analysis.security_updates,
                        "deprecations": analysis.deprecations,
                        "recommendations": analysis.recommendations,
                        "confidence": analysis.confidence,
                    }

                    with open(export, "w", encoding="utf-8") as f:
                        json.dump(export_data, f, indent=2, ensure_ascii=False)

                    print_success_panel(
                        "Analysis Exported",
                        f"Analysis results exported to: {export}",
                    )

                except Exception as e:
                    print_error_panel(
                        "Export Failed",
                        f"Failed to export analysis: {e}",
                    )

            log_operation_success(
                "repository analysis", repo=repo, provider=selected_provider
            )
        else:
            print_warning_panel(
                "No Analysis Available",
                f"No changes found to analyze for repository '{repo}'.\n\n"
                "This could mean:\n"
                "• The repository is up to date\n"
                "• Analysis is disabled for this repository\n"
                "• No recent changes were detected",
            )

    except Exception as e:
        log_operation_failure("repository analysis", e, repo=repo)
        print_error_panel(
            "Analysis Failed",
            f"Failed to analyze repository '{repo}': {e}",
        )


@main.command()
@click.option("--skill", "-s", help="Filter by skill")
@click.option("--label", "-l", help="Filter by label")
@click.option("--export", "-e", help="Export results to file")
@click.option("--limit", "-n", type=int, help="Limit number of results")
@click.option(
    "--min-confidence",
    "-c",
    type=float,
    default=0.1,
    help="Minimum confidence score (0.0-1.0)",
)
@click.option(
    "--personalized",
    "-p",
    is_flag=True,
    help="Include personalized recommendations based on contribution history",
)
@click.option(
    "--show-history",
    "-h",
    is_flag=True,
    help="Show contribution history analysis",
)
@click.option("--quiet", "-q", is_flag=True, help="Suppress output")
@click.pass_context
def discover(
    ctx: click.Context,
    skill: Optional[str],
    label: Optional[str],
    export: Optional[str],
    limit: Optional[int],
    min_confidence: float,
    personalized: bool,
    show_history: bool,
    quiet: bool,
) -> None:
    """Discover contribution opportunities with personalized recommendations.

    Scans repositories for issues matching your skills and interests.
    When --personalized is used, recommendations are enhanced with your
    contribution history and skill development patterns.
    """
    print_info_panel(
        "Discovering Contribution Opportunities",
        "Searching for issues that match your skills and interests...",
    )

    try:
        # Load configuration
        config_manager = get_config_manager()
        config = config_manager.load_config()

        if not config.repositories:
            print_error_panel(
                "No Repositories Configured",
                "Please add repositories to your configuration first using 'gitco init' or edit gitco-config.yml",
            )
            return

        # Create GitHub client
        github_credentials = config_manager.get_github_credentials()
        github_client = create_github_client(
            token=github_credentials.get("token"),  # type: ignore
            username=github_credentials.get("username"),  # type: ignore
            password=github_credentials.get("password"),  # type: ignore
            base_url=config.settings.github_api_url,
        )

        # Test GitHub connection
        if not github_client.test_connection():
            print_error_panel(
                "GitHub Connection Failed",
                "Unable to connect to GitHub API. Please check your credentials.",
            )
            return

        # Create discovery engine
        from .discovery import create_discovery_engine

        discovery_engine = create_discovery_engine(github_client, config)

        # Show contribution history analysis if requested
        if show_history or personalized:
            from .contribution_tracker import create_contribution_tracker

            tracker = create_contribution_tracker(config, github_client)

            try:
                stats = tracker.get_contribution_stats()

                if show_history:
                    print_info_panel(
                        "Contribution History Analysis",
                        f"📊 Total Contributions: {stats.total_contributions}\n"
                        f"🏢 Repositories: {stats.repositories_contributed_to}\n"
                        f"💡 Skills Developed: {len(stats.skills_developed)}\n"
                        f"⭐ Average Impact: {stats.average_impact_score:.2f}",
                    )

                    # Enhanced impact metrics
                    if (
                        stats.high_impact_contributions > 0
                        or stats.critical_contributions > 0
                    ):
                        impact_summary = (
                            f"🔥 High Impact: {stats.high_impact_contributions}"
                        )
                        if stats.critical_contributions > 0:
                            impact_summary += (
                                f" | 🚀 Critical: {stats.critical_contributions}"
                            )
                        print_info_panel("Impact Metrics", impact_summary)

                    # Trending analysis
                    if stats.contribution_velocity > 0:
                        velocity_trend = (
                            "📈" if stats.contribution_velocity > 0.1 else "📊"
                        )
                        print_info_panel(
                            "Contribution Velocity",
                            f"{velocity_trend} {stats.contribution_velocity:.2f} contributions/day (30d)",
                        )

                    # Impact trends
                    if stats.impact_trend_30d != 0 or stats.impact_trend_7d != 0:
                        trend_summary = ""
                        if stats.impact_trend_30d != 0:
                            trend_icon = "📈" if stats.impact_trend_30d > 0 else "📉"
                            trend_summary += (
                                f"{trend_icon} 30d: {stats.impact_trend_30d:+.2f} "
                            )
                        if stats.impact_trend_7d != 0:
                            trend_icon = "📈" if stats.impact_trend_7d > 0 else "📉"
                            trend_summary += (
                                f"{trend_icon} 7d: {stats.impact_trend_7d:+.2f}"
                            )
                        print_info_panel("Impact Trends", trend_summary)

                    # Trending skills
                    if stats.trending_skills:
                        trending_list = ", ".join(stats.trending_skills[:5])  # Top 5
                        print_info_panel(
                            "🚀 Trending Skills",
                            f"Skills with growing usage: {trending_list}",
                        )

                    if stats.declining_skills:
                        declining_list = ", ".join(stats.declining_skills[:5])  # Top 5
                        print_info_panel(
                            "📉 Declining Skills",
                            f"Skills with declining usage: {declining_list}",
                        )

                    # Advanced metrics
                    if stats.collaboration_score > 0 or stats.recognition_score > 0:
                        advanced_summary = ""
                        if stats.collaboration_score > 0:
                            advanced_summary += (
                                f"🤝 Collaboration: {stats.collaboration_score:.2f} "
                            )
                        if stats.recognition_score > 0:
                            advanced_summary += (
                                f"🏆 Recognition: {stats.recognition_score:.2f} "
                            )
                        if stats.influence_score > 0:
                            advanced_summary += (
                                f"💪 Influence: {stats.influence_score:.2f}"
                            )
                        print_info_panel("Advanced Metrics", advanced_summary)

                    if stats.skills_developed:
                        skills_list = ", ".join(sorted(stats.skills_developed))
                        print_info_panel(
                            "Skills Developed",
                            f"🎯 {skills_list}",
                        )

                    # Skill impact scores
                    if stats.skill_impact_scores:
                        top_skills = sorted(
                            stats.skill_impact_scores.items(),
                            key=lambda x: x[1],
                            reverse=True,
                        )[
                            :3
                        ]  # Top 3
                        skill_impact_summary = ""
                        for skill, impact in top_skills:
                            skill_impact_summary += f"{skill}: {impact:.2f} "
                        print_info_panel("Top Skill Impact", skill_impact_summary)

                    # Repository impact scores
                    if stats.repository_impact_scores:
                        top_repos = sorted(
                            stats.repository_impact_scores.items(),
                            key=lambda x: x[1],
                            reverse=True,
                        )[
                            :3
                        ]  # Top 3
                        repo_impact_summary = ""
                        for repo, impact in top_repos:
                            repo_name = repo.split("/")[-1]  # Just the repo name
                            repo_impact_summary += f"{repo_name}: {impact:.2f} "
                        print_info_panel("Top Repository Impact", repo_impact_summary)

                    if stats.recent_activity:
                        print_info_panel(
                            "Recent Activity",
                            f"🕒 Last {len(stats.recent_activity)} contributions:",
                        )
                        for i, contribution in enumerate(stats.recent_activity[:3], 1):
                            print_info_panel(
                                f"{i}. {contribution.issue_title}",
                                f"Repository: {contribution.repository}\n"
                                f"Impact: {contribution.impact_score:.2f}\n"
                                f"Skills: {', '.join(contribution.skills_used)}",
                            )
            except Exception as e:
                print_warning_panel(
                    "History Analysis Unavailable",
                    f"Could not analyze contribution history: {e}\n"
                    "Run 'gitco contributions sync-history --username YOUR_USERNAME' to sync your history.",
                )

        # Discover opportunities with enhanced personalization
        recommendations = discovery_engine.discover_opportunities(
            skill_filter=skill,
            label_filter=label,
            limit=limit,
            min_confidence=min_confidence,
            include_personalization=personalized,
        )

        if not recommendations:
            print_warning_panel(
                "No Opportunities Found",
                "No matching issues found with the current filters. Try adjusting your search criteria.",
            )
            return

        # Display results with enhanced information
        print_success_panel(
            "Discovery Results",
            f"Found {len(recommendations)} contribution opportunities!"
            + (" (with personalized scoring)" if personalized else ""),
        )

        # Group recommendations by type if personalized
        if personalized:
            high_confidence = [r for r in recommendations if r.overall_score > 0.7]
            medium_confidence = [
                r for r in recommendations if 0.4 <= r.overall_score <= 0.7
            ]
            low_confidence = [r for r in recommendations if r.overall_score < 0.4]

            if high_confidence:
                print_info_panel(
                    "🎯 High Confidence Matches",
                    f"Found {len(high_confidence)} excellent matches based on your history",
                )
                for i, recommendation in enumerate(high_confidence, 1):
                    print_issue_recommendation(recommendation, i)

            if medium_confidence:
                print_info_panel(
                    "⭐ Good Matches",
                    f"Found {len(medium_confidence)} good matches for skill development",
                )
                for i, recommendation in enumerate(medium_confidence, 1):
                    print_issue_recommendation(recommendation, len(high_confidence) + i)

            if low_confidence:
                print_info_panel(
                    "💡 Exploration Opportunities",
                    f"Found {len(low_confidence)} opportunities to explore new areas",
                )
                for i, recommendation in enumerate(low_confidence, 1):
                    print_issue_recommendation(
                        recommendation,
                        len(high_confidence) + len(medium_confidence) + i,
                    )
        else:
            # Standard display
            for i, recommendation in enumerate(recommendations, 1):
                print_issue_recommendation(recommendation, i)

        # Export results if requested
        if export:
            export_discovery_results(recommendations, export)

        # Show personalized insights if enabled
        if personalized:
            print_info_panel(
                "Personalized Insights",
                "💡 Tips based on your contribution history:\n"
                "• Focus on repositories where you've had high impact\n"
                "• Consider exploring new skills to diversify your portfolio\n"
                "• Look for issues that combine multiple skills you've used\n"
                "• Prioritize issues in active repositories with good engagement",
            )

        print_success_panel("Discovery Completed", "✅ Discovery completed!")

    except Exception as e:
        print_error_panel(
            "Discovery Failed",
            f"An error occurred during discovery: {str(e)}",
        )
        if ctx.obj.get("verbose"):
            raise


@main.command()
@click.option("--repo", "-r", help="Show specific repository")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed information")
@click.option("--export", "-e", help="Export status to file")
@click.option(
    "--overview", "-o", is_flag=True, help="Show repository overview dashboard"
)
@click.option(
    "--filter",
    "-f",
    help="Filter repositories by status (healthy, needs_attention, critical)",
)
@click.option(
    "--sort", "-s", help="Sort repositories by metric (health, activity, stars, forks)"
)
@click.option("--quiet", "-q", is_flag=True, help="Suppress output")
@click.pass_context
def status(
    ctx: click.Context,
    repo: Optional[str],
    detailed: bool,
    export: Optional[str],
    overview: bool,
    filter: Optional[str],
    sort: Optional[str],
    quiet: bool,
) -> None:
    """Show repository status and overview.

    Displays the current status of your repositories and their sync state.
    Provides comprehensive overview with filtering and sorting options.
    """
    get_logger()
    log_operation_start("repository status check")

    try:
        config_manager = get_config_manager()
        config = config_manager.load_config()
        github_client = create_github_client()

        from .health_metrics import create_health_calculator

        health_calculator = create_health_calculator(config, github_client)

        if repo:
            # Show status for specific repository
            repo_config = None
            for r in config.repositories:
                if hasattr(r, "get") and r.get("name") == repo:
                    repo_config = r
                    break

            if not repo_config:
                print_error_panel(
                    "Repository Not Found",
                    f"Repository '{repo}' not found in configuration.",
                )
                return

            metrics = health_calculator.calculate_repository_health(repo_config)  # type: ignore
            _print_repository_health(metrics, detailed)

        else:
            # Show status for all repositories
            summary = health_calculator.calculate_health_summary(config.repositories)  # type: ignore

            if overview:
                _print_repository_overview(
                    config.repositories, health_calculator, filter, sort
                )
            else:
                _print_health_summary(summary, detailed)

                if detailed:
                    print_info_panel(
                        "Detailed Repository Health", "Calculating detailed metrics..."
                    )
                    for repo_config in config.repositories:
                        metrics = health_calculator.calculate_repository_health(repo_config)  # type: ignore
                        _print_repository_health(metrics, detailed=True)

        # Export if requested
        if export:
            export_health_data(config.repositories, health_calculator, export)

        log_operation_success("repository status check")
        print_success_panel(
            "Status Check Completed", "✅ Repository health analysis completed!"
        )

    except Exception as e:
        log_operation_failure("repository status check", error=e)
        print_error_panel(
            "Status Check Failed", f"Failed to check repository status: {e}"
        )


def _print_health_summary(summary: Any, detailed: bool = False) -> None:
    """Print health summary information."""
    from rich.table import Table
    from rich.text import Text

    # Create summary table
    table = Table(
        title="Repository Health Summary", show_header=True, header_style="bold magenta"
    )
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("Total Repositories", str(summary.total_repositories))
    table.add_row(
        "Healthy Repositories",
        f"{summary.healthy_repositories} ({(summary.healthy_repositories/summary.total_repositories*100):.1f}%)",
    )
    table.add_row(
        "Needs Attention",
        f"{summary.needs_attention_repositories} ({(summary.needs_attention_repositories/summary.total_repositories*100):.1f}%)",
    )
    table.add_row(
        "Critical Repositories",
        f"{summary.critical_repositories} ({(summary.critical_repositories/summary.total_repositories*100):.1f}%)",
    )
    table.add_row("", "")  # Empty row for spacing
    table.add_row("Active (7 days)", str(summary.active_repositories_7d))
    table.add_row("Active (30 days)", str(summary.active_repositories_30d))
    table.add_row("Up to Date", str(summary.up_to_date_repositories))
    table.add_row("Behind Upstream", str(summary.behind_repositories))
    table.add_row("Diverged", str(summary.diverged_repositories))
    table.add_row("", "")  # Empty row for spacing
    table.add_row("High Engagement", str(summary.high_engagement_repositories))
    table.add_row("Low Engagement", str(summary.low_engagement_repositories))
    table.add_row("Average Activity Score", f"{summary.average_activity_score:.2f}")

    console.print(table)

    # Show trending repositories
    if summary.trending_repositories:
        trending_text = Text("📈 Trending Repositories:", style="bold green")
        console.print(trending_text)
        for repo in summary.trending_repositories:
            console.print(f"  • {repo}")

    # Show declining repositories
    if summary.declining_repositories:
        declining_text = Text("📉 Declining Repositories:", style="bold red")
        console.print(declining_text)
        for repo in summary.declining_repositories:
            console.print(f"  • {repo}")

    if detailed:
        # Show additional metrics
        metrics_table = Table(
            title="Repository Metrics", show_header=True, header_style="bold magenta"
        )
        metrics_table.add_column("Metric", style="cyan")
        metrics_table.add_column("Value", style="green")

        metrics_table.add_row("Total Stars", str(summary.total_stars))
        metrics_table.add_row("Total Forks", str(summary.total_forks))
        metrics_table.add_row("Total Open Issues", str(summary.total_open_issues))

        console.print(metrics_table)


def _print_repository_health(metrics: Any, detailed: bool = False) -> None:
    """Print detailed repository health information."""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    # Determine health status color
    status_colors = {
        "excellent": "green",
        "good": "blue",
        "fair": "yellow",
        "poor": "orange",
        "critical": "red",
        "unknown": "white",
    }
    status_color = status_colors.get(metrics.health_status, "white")

    # Create health status panel
    status_text = Text(
        f"Health Status: {metrics.health_status.upper()}", style=f"bold {status_color}"
    )
    score_text = Text(f"Score: {metrics.overall_health_score:.2f}", style="cyan")

    panel_content = f"{status_text}\n{score_text}"
    panel = Panel(
        panel_content,
        title=f"Repository: {metrics.repository_name}",
        border_style=status_color,
    )
    console.print(panel)

    # Create detailed metrics table
    table = Table(
        title="Repository Metrics", show_header=True, header_style="bold magenta"
    )
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    # Activity metrics
    table.add_row("Recent Commits (7d)", str(metrics.recent_commits_7d))
    table.add_row("Recent Commits (30d)", str(metrics.recent_commits_30d))
    table.add_row("Total Commits", str(metrics.total_commits))
    if metrics.last_commit_days_ago is not None:
        table.add_row("Last Commit", f"{metrics.last_commit_days_ago} days ago")

    # GitHub metrics
    table.add_row("Stars", str(metrics.stars_count))
    table.add_row("Forks", str(metrics.forks_count))
    table.add_row("Open Issues", str(metrics.open_issues_count))
    if metrics.language:
        table.add_row("Language", metrics.language)

    # Sync metrics
    table.add_row("Sync Status", metrics.sync_status)
    if metrics.days_since_last_sync is not None:
        table.add_row("Days Since Sync", str(metrics.days_since_last_sync))
    table.add_row("Uncommitted Changes", "Yes" if metrics.uncommitted_changes else "No")

    # Engagement metrics
    table.add_row("Engagement Score", f"{metrics.contributor_engagement_score:.2f}")
    if metrics.issue_response_time_avg is not None:
        table.add_row(
            "Avg Issue Response", f"{metrics.issue_response_time_avg:.1f} hours"
        )

    # Trending metrics
    table.add_row("Stars Growth (30d)", str(metrics.stars_growth_30d))
    table.add_row("Forks Growth (30d)", str(metrics.forks_growth_30d))

    console.print(table)

    if detailed and metrics.topics:
        topics_text = Text("Topics:", style="bold")
        console.print(topics_text)
        console.print(f"  {', '.join(metrics.topics)}")


def _print_repository_overview(
    repositories: Any,
    health_calculator: Any,
    filter_status: Optional[str] = None,
    sort_by: Optional[str] = None,
) -> None:
    """Print comprehensive repository overview dashboard."""
    from rich import box
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.table import Table

    # Calculate metrics for all repositories
    all_metrics = []
    for repo_config in repositories:
        metrics = health_calculator.calculate_repository_health(repo_config)
        all_metrics.append(metrics)

    # Apply filtering
    if filter_status:
        filter_status = filter_status.lower()
        if filter_status == "healthy":
            all_metrics = [
                m for m in all_metrics if m.health_status in ["excellent", "good"]
            ]
        elif filter_status == "needs_attention":
            all_metrics = [
                m for m in all_metrics if m.health_status in ["fair", "poor"]
            ]
        elif filter_status == "critical":
            all_metrics = [m for m in all_metrics if m.health_status == "critical"]

    # Apply sorting
    if sort_by:
        sort_by = sort_by.lower()
        if sort_by == "health":
            all_metrics.sort(key=lambda x: x.overall_health_score, reverse=True)
        elif sort_by == "activity":
            all_metrics.sort(key=lambda x: x.recent_commits_30d, reverse=True)
        elif sort_by == "stars":
            all_metrics.sort(key=lambda x: x.stars_count, reverse=True)
        elif sort_by == "forks":
            all_metrics.sort(key=lambda x: x.forks_count, reverse=True)
        elif sort_by == "engagement":
            all_metrics.sort(key=lambda x: x.contributor_engagement_score, reverse=True)

    # Create overview table
    table = Table(
        title="Repository Overview Dashboard",
        show_header=True,
        header_style="bold magenta",
        box=box.ROUNDED,
    )

    # Add columns
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("Health", style="green", justify="center")
    table.add_column("Sync", style="blue", justify="center")
    table.add_column("Activity", style="yellow", justify="center")
    table.add_column("Stars", style="magenta", justify="right")
    table.add_column("Forks", style="cyan", justify="right")
    table.add_column("Issues", style="red", justify="right")
    table.add_column("Engagement", style="green", justify="center")

    # Add rows
    for metrics in all_metrics:
        # Health status with color coding
        health_emoji = {
            "excellent": "🟢",
            "good": "🟢",
            "fair": "🟡",
            "poor": "🟠",
            "critical": "🔴",
            "unknown": "⚪",
        }.get(metrics.health_status, "⚪")

        health_text = f"{health_emoji} {metrics.health_status.title()}"

        # Sync status with color coding
        sync_emoji = {
            "up_to_date": "✅",
            "behind": "⚠️",
            "ahead": "🔄",
            "diverged": "❌",
            "unknown": "❓",
        }.get(metrics.sync_status, "❓")

        sync_text = f"{sync_emoji} {metrics.sync_status.replace('_', ' ').title()}"

        # Activity indicator
        activity_score = min(metrics.recent_commits_30d, 10)  # Cap at 10 for display
        activity_bar = "█" * activity_score + "░" * (10 - activity_score)
        activity_text = f"{activity_bar} {metrics.recent_commits_30d}"

        # Engagement score
        engagement_percent = int(metrics.contributor_engagement_score * 100)
        engagement_text = f"{engagement_percent}%"

        table.add_row(
            metrics.repository_name,
            health_text,
            sync_text,
            activity_text,
            str(metrics.stars_count),
            str(metrics.forks_count),
            str(metrics.open_issues_count),
            engagement_text,
        )

    console.print(table)

    # Show summary statistics
    if all_metrics:
        total_repos = len(all_metrics)
        healthy_count = len(
            [m for m in all_metrics if m.health_status in ["excellent", "good"]]
        )

        up_to_date_count = len(
            [m for m in all_metrics if m.sync_status == "up_to_date"]
        )

        total_stars = sum(m.stars_count for m in all_metrics)

        avg_engagement = (
            sum(m.contributor_engagement_score for m in all_metrics) / total_repos
        )

        # Create summary panels
        summary_panels = [
            Panel(
                f"[bold green]{healthy_count}[/bold green] / {total_repos}\nHealthy",
                title="Health Status",
                border_style="green",
            ),
            Panel(
                f"[bold blue]{up_to_date_count}[/bold blue] / {total_repos}\nUp to Date",
                title="Sync Status",
                border_style="blue",
            ),
            Panel(
                f"[bold magenta]{total_stars:,}[/bold magenta]\nTotal Stars",
                title="Popularity",
                border_style="magenta",
            ),
            Panel(
                f"[bold yellow]{avg_engagement:.1%}[/bold yellow]\nAvg Engagement",
                title="Community",
                border_style="yellow",
            ),
        ]

        console.print("\n")
        console.print(Columns(summary_panels, equal=True, expand=True))

        # Show alerts for repositories needing attention
        alerts = []
        for metrics in all_metrics:
            if metrics.health_status in ["poor", "critical"]:
                alerts.append(f"⚠️  {metrics.repository_name} needs attention")
            elif metrics.sync_status == "behind":
                alerts.append(f"🔄 {metrics.repository_name} is behind upstream")
            elif metrics.sync_status == "diverged":
                alerts.append(f"❌ {metrics.repository_name} has diverged from upstream")

        if alerts:
            console.print("\n")
            alert_panel = Panel(
                "\n".join(alerts[:5]),  # Show top 5 alerts
                title="⚠️  Alerts",
                border_style="red",
            )
            console.print(alert_panel)


@main.command()
@click.option(
    "--export", "-e", help="Export performance summary to file (.json or .csv)"
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "csv"]),
    default="json",
    help="Export format",
)
@click.pass_context
def logs(ctx: click.Context, export: Optional[str], format: str) -> None:
    """View and export detailed logging information.

    Shows performance summaries and allows exporting log data for analysis.
    """
    from .utils.logging import get_gitco_logger

    gitco_logger = get_gitco_logger()

    if export:
        gitco_logger.export_logs(export, format)
        print_success_panel(
            "Logs exported successfully",
            f"Performance summary exported to: {export}\nFormat: {format.upper()}",
        )
    else:
        # Print performance summary
        gitco_logger.print_performance_summary()


@main.command()
@click.pass_context
def help(ctx: click.Context) -> None:
    """Show detailed help information.

    Provides comprehensive help and usage examples for GitCo commands.
    """
    print_info_panel(
        "GitCo Help",
        "This provides comprehensive help and usage examples for GitCo commands.",
    )
    click.echo()
    click.echo(
        "GitCo is a CLI tool for intelligent OSS fork management and contribution discovery."
    )
    click.echo()
    click.echo("Basic Commands:")
    click.echo("  init      Initialize configuration")
    click.echo("  config    Validate configuration")
    click.echo("  sync      Synchronize repositories")
    click.echo("  analyze   Analyze changes with AI")
    click.echo("  discover  Find contribution opportunities")
    click.echo("  status    Show repository status")
    click.echo("  help      Show this help message")
    click.echo()
    click.echo("Contribution Tracking:")
    click.echo("  contributions sync-history  Sync contribution history from GitHub")
    click.echo("  contributions stats         Show contribution statistics")
    click.echo("  contributions recommendations  Get personalized recommendations")
    click.echo()
    click.echo("For detailed help on any command, use:")
    click.echo("  gitco <command> --help")
    click.echo()
    click.echo("Examples:")
    click.echo("  gitco init")
    click.echo("  gitco config validate")
    click.echo("  gitco sync --repo django")
    click.echo("  gitco analyze --repo fastapi")
    click.echo("  gitco discover --skill python")
    click.echo("  gitco status --detailed")
    click.echo("  gitco validate-repo --path ~/code/django")
    click.echo("  gitco validate-repo --recursive --detailed")


@main.group()
@click.pass_context
def config(ctx: click.Context) -> None:
    """Configuration management commands."""
    pass


@config.command()
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Validate configuration file.

    Checks the configuration file for errors and displays validation results.
    """
    get_logger()
    log_operation_start("configuration validation")

    try:
        config_manager = ConfigManager()
        config = config_manager.load_config()

        errors = config_manager.validate_config(config)

        if not errors:
            log_operation_success(
                "configuration validation", repo_count=len(config.repositories)
            )
            print_success_panel(
                "Configuration is valid!",
                f"Found {len(config.repositories)} repositories\n"
                f"LLM provider: {config.settings.llm_provider}",
            )
        else:
            log_operation_failure(
                "configuration validation", ValidationError("Configuration has errors")
            )
            handle_validation_errors(errors, "Configuration")
            print_error_panel(
                "Configuration has errors", "❌ Configuration has errors:\n"
            )
            for error in errors:
                print_error_panel("Error", f"  - {error}")
            sys.exit(1)

    except FileNotFoundError as e:
        log_operation_failure("configuration validation", e)
        print_error_panel(
            "Configuration file not found",
            "Run 'gitco init' to create a configuration file.",
        )
        sys.exit(1)
    except Exception as e:
        log_operation_failure("configuration validation", e)
        print_error_panel("Error validating configuration", str(e))
        sys.exit(1)


@config.command()
@click.pass_context
def config_status(ctx: click.Context) -> None:
    """Show configuration status.

    Displays information about the current configuration.
    """
    get_logger()
    log_operation_start("configuration status")

    try:
        config_manager = ConfigManager()
        config = config_manager.load_config()

        log_operation_success(
            "configuration status", repo_count=len(config.repositories)
        )
        print_success_panel(
            "Configuration Status", "Configuration Status\n===================\n\n"
        )

        print_info_panel(
            "Configuration File", f"Configuration file: {config_manager.config_path}"
        )
        print_info_panel("Repositories", f"Repositories: {len(config.repositories)}")
        print_info_panel(
            "LLM Provider", f"LLM provider: {config.settings.llm_provider}"
        )
        print_info_panel(
            "Analysis Enabled", f"Analysis enabled: {config.settings.analysis_enabled}"
        )
        print_info_panel(
            "Max Repos per Batch",
            f"Max repos per batch: {config.settings.max_repos_per_batch}",
        )

        if config.repositories:
            print_info_panel("Repositories", "Repositories:\n")
            for repo in config.repositories:
                print_info_panel(
                    "Repository", f"  - {repo.name}: {repo.fork} -> {repo.upstream}"
                )

    except FileNotFoundError as e:
        log_operation_failure("configuration status", e)
        print_error_panel(
            "Configuration file not found",
            "Run 'gitco init' to create a configuration file.",
        )
        sys.exit(1)
    except Exception as e:
        log_operation_failure("configuration status", e)
        print_error_panel("Error reading configuration", str(e))
        sys.exit(1)


@main.group()
@click.pass_context
def upstream(ctx: click.Context) -> None:
    """Upstream remote management commands."""
    pass


@upstream.command()
@click.option("--repo", "-r", required=True, help="Repository path")
@click.option("--url", "-u", required=True, help="Upstream repository URL")
@click.pass_context
def add(ctx: click.Context, repo: str, url: str) -> None:
    """Add upstream remote to a repository.

    Adds or updates the upstream remote for the specified repository.
    """
    log_operation_start("upstream remote addition", repo=repo, url=url)

    try:
        git_manager = GitRepositoryManager()

        # Validate repository path
        is_valid, errors = git_manager.validate_repository_path(repo)
        if not is_valid:
            log_operation_failure(
                "upstream remote addition", ValidationError("Invalid repository path")
            )
            print_error_panel("Invalid Repository Path", "❌ Invalid repository path:\n")
            for error in errors:
                print_error_panel("Error", f"  - {error}")
            sys.exit(1)

        # Add upstream remote
        success = git_manager.setup_upstream_remote(repo, url)

        if success:
            log_operation_success("upstream remote addition", repo=repo, url=url)
            print_success_panel(
                "Upstream remote added successfully!",
                f"Repository: {repo}\nUpstream URL: {url}",
            )
        else:
            log_operation_failure(
                "upstream remote addition", Exception("Failed to add upstream remote")
            )
            print_error_panel(
                "Failed to add upstream remote", "❌ Failed to add upstream remote"
            )
            sys.exit(1)

    except Exception as e:
        log_operation_failure("upstream remote addition", e)
        print_error_panel("Error adding upstream remote", str(e))
        sys.exit(1)


@upstream.command()
@click.option("--repo", "-r", required=True, help="Repository path")
@click.pass_context
def remove(ctx: click.Context, repo: str) -> None:
    """Remove upstream remote from a repository.

    Removes the upstream remote if it exists.
    """
    log_operation_start("upstream remote removal", repo=repo)

    try:
        git_manager = GitRepositoryManager()

        # Validate repository path
        is_valid, errors = git_manager.validate_repository_path(repo)
        if not is_valid:
            log_operation_failure(
                "upstream remote removal", ValidationError("Invalid repository path")
            )
            print_error_panel("Invalid Repository Path", "❌ Invalid repository path:\n")
            for error in errors:
                print_error_panel("Error", f"  - {error}")
            sys.exit(1)

        # Remove upstream remote
        success = git_manager.remove_upstream_remote(repo)

        if success:
            log_operation_success("upstream remote removal", repo=repo)
            print_success_panel(
                "Upstream remote removed successfully!", f"Repository: {repo}"
            )
        else:
            log_operation_failure(
                "upstream remote removal", Exception("Failed to remove upstream remote")
            )
            print_error_panel(
                "Failed to remove upstream remote",
                "❌ Failed to remove upstream remote",
            )
            sys.exit(1)

    except Exception as e:
        log_operation_failure("upstream remote removal", e)
        print_error_panel("Error removing upstream remote", str(e))
        sys.exit(1)


@upstream.command()
@click.option("--repo", "-r", required=True, help="Repository path")
@click.option("--url", "-u", required=True, help="New upstream repository URL")
@click.pass_context
def update(ctx: click.Context, repo: str, url: str) -> None:
    """Update upstream remote URL for a repository.

    Updates the URL of the existing upstream remote.
    """
    log_operation_start("upstream remote update", repo=repo, url=url)

    try:
        git_manager = GitRepositoryManager()

        # Validate repository path
        is_valid, errors = git_manager.validate_repository_path(repo)
        if not is_valid:
            log_operation_failure(
                "upstream remote update", ValidationError("Invalid repository path")
            )
            print_error_panel("Invalid Repository Path", "❌ Invalid repository path:\n")
            for error in errors:
                print_error_panel("Error", f"  - {error}")
            sys.exit(1)

        # Update upstream remote
        success = git_manager.update_upstream_remote(repo, url)

        if success:
            log_operation_success("upstream remote update", repo=repo, url=url)
            print_success_panel(
                "Upstream remote updated successfully!",
                f"Repository: {repo}\nNew upstream URL: {url}",
            )
        else:
            log_operation_failure(
                "upstream remote update", Exception("Failed to update upstream remote")
            )
            print_error_panel(
                "Failed to update upstream remote",
                "❌ Failed to update upstream remote",
            )
            sys.exit(1)

    except Exception as e:
        log_operation_failure("upstream remote update", e)
        print_error_panel("Error updating upstream remote", str(e))
        sys.exit(1)


@upstream.command()
@click.option("--repo", "-r", required=True, help="Repository path")
@click.pass_context
def validate_upstream(ctx: click.Context, repo: str) -> None:
    """Validate upstream remote for a repository.

    Checks if the upstream remote is properly configured and accessible.
    """
    log_operation_start("upstream remote validation", repo=repo)

    try:
        git_manager = GitRepositoryManager()

        # Validate repository path
        is_valid, errors = git_manager.validate_repository_path(repo)
        if not is_valid:
            log_operation_failure(
                "upstream remote validation", ValidationError("Invalid repository path")
            )
            print_error_panel("Invalid Repository Path", "❌ Invalid repository path:\n")
            for error in errors:
                print_error_panel("Error", f"  - {error}")
            sys.exit(1)

        # Validate upstream remote
        validation = git_manager.validate_upstream_remote(repo)

        log_operation_success("upstream remote validation", repo=repo)
        print_success_panel(
            f"Repository: {repo}", "Upstream Remote Status\n======================\n\n"
        )

        if validation["has_upstream"]:
            print_info_panel("Upstream URL", f"Upstream URL: {validation['url']}")

            if validation["is_valid"]:
                print_success_panel(
                    "Upstream remote is valid and accessible",
                    "✅ Upstream remote is valid and accessible",
                )
                if validation.get("accessible", False):
                    print_success_panel(
                        "Upstream remote is accessible",
                        "✅ Upstream remote is accessible",
                    )
            else:
                print_error_panel(
                    "Upstream remote validation failed", f"Error: {validation['error']}"
                )
        else:
            print_error_panel(
                "No upstream remote configured", "❌ No upstream remote configured"
            )

    except Exception as e:
        log_operation_failure("upstream remote validation", e)
        print_error_panel("Error validating upstream remote", str(e))
        sys.exit(1)


@upstream.command()
@click.option("--repo", "-r", required=True, help="Repository path")
@click.pass_context
def fetch(ctx: click.Context, repo: str) -> None:
    """Fetch latest changes from upstream.

    Fetches the latest changes from the upstream remote.
    """
    log_operation_start("upstream fetch", repo=repo)

    try:
        git_manager = GitRepositoryManager()

        # Validate repository path
        is_valid, errors = git_manager.validate_repository_path(repo)
        if not is_valid:
            log_operation_failure(
                "upstream fetch", ValidationError("Invalid repository path")
            )
            print_error_panel("Invalid Repository Path", "❌ Invalid repository path:\n")
            for error in errors:
                print_error_panel("Error", f"  - {error}")
            sys.exit(1)

        # Get repository instance
        repository = git_manager.get_repository_info(repo)
        if not repository["is_git_repository"]:
            log_operation_failure(
                "upstream fetch", Exception("Not a valid Git repository")
            )
            print_error_panel(
                "Not a valid Git repository", "❌ Not a valid Git repository"
            )
            sys.exit(1)

        # Fetch from upstream
        git_repo = GitRepository(repo)
        success = git_repo.fetch_upstream()

        if success:
            log_operation_success("upstream fetch", repo=repo)
            print_success_panel(
                "Successfully fetched from upstream!", f"Repository: {repo}"
            )
        else:
            log_operation_failure(
                "upstream fetch", Exception("Failed to fetch from upstream")
            )
            print_error_panel(
                "Failed to fetch from upstream", "❌ Failed to fetch from upstream"
            )
            sys.exit(1)

    except Exception as e:
        log_operation_failure("upstream fetch", e)
        print_error_panel("Error fetching from upstream", str(e))
        sys.exit(1)


@upstream.command()
@click.option("--repo", "-r", required=True, help="Repository path")
@click.option("--branch", "-b", help="Branch to merge (default: default branch)")
@click.option(
    "--strategy",
    "-s",
    type=click.Choice(["ours", "theirs", "manual"]),
    default="ours",
    help="Conflict resolution strategy",
)
@click.option("--abort", "-a", is_flag=True, help="Abort current merge")
@click.option("--resolve", is_flag=True, help="Resolve conflicts automatically")
@click.pass_context
def merge(
    ctx: click.Context,
    repo: str,
    branch: Optional[str],
    strategy: str,
    abort: bool,
    resolve: bool,
) -> None:
    """Merge upstream changes into current branch.

    Merges the latest changes from upstream into the current branch with conflict detection.
    """
    log_operation_start("upstream merge", repo=repo, branch=branch, strategy=strategy)

    try:
        git_manager = GitRepositoryManager()

        # Validate repository path
        is_valid, errors = git_manager.validate_repository_path(repo)
        if not is_valid:
            log_operation_failure(
                "upstream merge", ValidationError("Invalid repository path")
            )
            print_error_panel("Invalid Repository Path", "❌ Invalid repository path:\n")
            for error in errors:
                print_error_panel("Error", f"  - {error}")
            sys.exit(1)

        # Get repository instance
        repository = git_manager.get_repository_info(repo)
        if not repository["is_git_repository"]:
            log_operation_failure(
                "upstream merge", Exception("Not a valid Git repository")
            )
            print_error_panel(
                "Not a valid Git repository", "❌ Not a valid Git repository"
            )
            sys.exit(1)

        git_repo = GitRepository(repo)

        # Check merge status first
        merge_status = git_repo.get_merge_status()

        if abort:
            # Abort current merge
            if merge_status["in_merge"]:
                success = git_repo.abort_merge()
                if success:
                    log_operation_success("upstream merge abort", repo=repo)
                    print_success_panel(
                        "Merge aborted successfully!", "✅ Successfully aborted merge!"
                    )
                else:
                    log_operation_failure(
                        "upstream merge abort", Exception("Failed to abort merge")
                    )
                    print_error_panel(
                        "Failed to abort merge", "❌ Failed to abort merge"
                    )
                    sys.exit(1)
            else:
                print_info_panel(
                    "No active merge to abort", "ℹ️  No active merge to abort"
                )
            return

        if resolve:
            # Resolve conflicts
            if merge_status["in_merge"] and merge_status["conflicts"]:
                success = git_repo.resolve_conflicts(strategy)
                if success:
                    log_operation_success(
                        "upstream merge resolve", repo=repo, strategy=strategy
                    )
                    print_success_panel(
                        f"Conflicts resolved using {strategy} strategy!",
                        f"✅ Successfully resolved conflicts using {strategy} strategy!",
                    )
                else:
                    log_operation_failure(
                        "upstream merge resolve",
                        Exception("Failed to resolve conflicts"),
                    )
                    print_error_panel(
                        "Failed to resolve conflicts", "❌ Failed to resolve conflicts"
                    )
                    sys.exit(1)
            else:
                print_info_panel(
                    "No conflicts to resolve", "ℹ️  No conflicts to resolve"
                )
            return

        # Perform merge operation
        merge_result = git_repo.merge_upstream_branch(branch)

        if merge_result["success"]:
            log_operation_success("upstream merge", repo=repo, branch=branch)
            print_success_panel(
                "Successfully merged upstream changes!", f"Repository: {repo}\n"
            )
            if merge_result.get("message"):
                print_info_panel("Message", f"Message: {merge_result['message']}")
            if merge_result.get("merge_commit"):
                print_info_panel(
                    "Merge Commit", f"Merge commit: {merge_result['merge_commit']}"
                )
        else:
            if merge_result.get("conflicts"):
                log_operation_failure(
                    "upstream merge", Exception("Merge conflicts detected")
                )
                print_error_panel(
                    "Merge Conflicts Detected",
                    "⚠️  Merge conflicts detected!\n\n"
                    f"Repository: {repo}\n"
                    "Conflicted files:\n",
                )
                for conflict in merge_result["conflicts"]:
                    print_error_panel("Conflict", f"  - {conflict}")
                print_info_panel(
                    "Resolution Options",
                    "To resolve conflicts, use:\n"
                    f"  gitco upstream merge --repo {repo} --resolve --strategy ours\n"
                    f"  gitco upstream merge --repo {repo} --resolve --strategy theirs\n",
                )
                print_info_panel(
                    "Abort Merge",
                    "Or abort the merge with:\n"
                    f"  gitco upstream merge --repo {repo} --abort",
                )
            else:
                log_operation_failure(
                    "upstream merge",
                    Exception(merge_result.get("error", "Unknown error")),
                )
                print_error_panel(
                    "Merge failed",
                    f"❌ Merge failed: {merge_result.get('error', 'Unknown error')}",
                )
            sys.exit(1)

    except Exception as e:
        log_operation_failure("upstream merge", e)
        print_error_panel("Error merging upstream changes", str(e))
        sys.exit(1)


@main.command()
@click.option("--path", "-p", help="Path to validate (default: current directory)")
@click.option("--recursive", "-r", is_flag=True, help="Recursively find repositories")
@click.option(
    "--detailed", "-d", is_flag=True, help="Show detailed repository information"
)
@click.pass_context
def validate_repo(
    ctx: click.Context, path: Optional[str], recursive: bool, detailed: bool
) -> None:
    """Validate Git repositories.

    Checks if the specified path is a valid Git repository and provides detailed
    information about its status, remotes, and sync state.
    """
    log_operation_start("repository validation", path=path, recursive=recursive)

    try:
        git_manager = GitRepositoryManager()
        target_path = path or "."

        if recursive:
            # Find all repositories in the directory tree
            repositories = git_manager.detect_repositories(target_path)

            if not repositories:
                log_operation_failure(
                    "repository validation", Exception("No Git repositories found")
                )
                print_error_panel(
                    "No Git Repositories Found",
                    "❌ No Git repositories found in the specified path.",
                )
                sys.exit(1)

            log_operation_success("repository validation", repo_count=len(repositories))
            print_success_panel(
                f"Found {len(repositories)} Git repositories!",
                f"Found {len(repositories)} Git repositories:\n",
            )
            for repo in repositories:
                status = repo.get_repository_status()
                print_info_panel("Repository", f"📁 {status['path']}\n")
                print_info_panel(
                    "Branch", f"   Branch: {status['current_branch'] or 'unknown'}"
                )
                print_info_panel(
                    "Default Branch",
                    f"   Default: {status['default_branch'] or 'unknown'}",
                )
                print_info_panel("Remotes", f"   Remotes: {len(status['remotes'])}")
                print_info_panel(
                    "Clean", f"   Clean: {'✅' if status['is_clean'] else '❌'}"
                )

                if detailed:
                    sync_status = git_manager.check_repository_sync_status(
                        str(repo.path)
                    )
                    if sync_status["is_syncable"]:
                        print_info_panel(
                            "Sync Status",
                            f"   Sync: {sync_status['behind_upstream']} behind, {sync_status['ahead_upstream']} ahead",
                        )
                    else:
                        print_error_panel(
                            "Sync Status", f"   Sync: {sync_status['error']}"
                        )

                print_info_panel("Repository", "")

        else:
            # Validate single repository
            is_valid, errors = git_manager.validate_repository_path(target_path)

            if is_valid:
                log_operation_success("repository validation", path=target_path)
                print_success_panel("Valid Git repository!", "✅ Valid Git repository!")

                if detailed:
                    status = git_manager.get_repository_info(target_path)
                    sync_status = git_manager.check_repository_sync_status(target_path)

                    print_info_panel("Path", f"Path: {status['path']}")
                    print_info_panel(
                        "Current Branch", f"Current branch: {status['current_branch']}"
                    )
                    print_info_panel(
                        "Default Branch", f"Default branch: {status['default_branch']}"
                    )
                    print_info_panel(
                        "Remotes", f"Remotes: {', '.join(status['remotes'].keys())}"
                    )
                    print_info_panel(
                        "Uncommitted Changes",
                        f"Has uncommitted changes: {'Yes' if status['has_uncommitted_changes'] else 'No'}",
                    )
                    print_info_panel(
                        "Untracked Files",
                        f"Has untracked files: {'Yes' if status['has_untracked_files'] else 'No'}",
                    )

                    if sync_status["is_syncable"]:
                        print_info_panel(
                            "Sync Status",
                            f"Sync status: {sync_status['behind_upstream']} behind, {sync_status['ahead_upstream']} ahead",
                        )
                        if sync_status["diverged"]:
                            print_warning_panel(
                                "Repository Diverged",
                                "⚠️  Repository has diverged from upstream",
                            )
                    else:
                        print_error_panel(
                            "Sync Status", f"Sync status: {sync_status['error']}"
                        )
            else:
                log_operation_failure(
                    "repository validation",
                    ValidationError("Repository validation failed"),
                )
                print_error_panel(
                    "Invalid Git repository", "❌ Invalid Git repository:\n"
                )
                for error in errors:
                    print_error_panel("Error", f"  - {error}")
                sys.exit(1)

    except Exception as e:
        log_operation_failure("repository validation", e)
        print_error_panel("Error validating repository", str(e))
        sys.exit(1)


@main.group()
@click.pass_context
def github(ctx: click.Context) -> None:
    """GitHub API operations."""
    pass


@github.command()
@click.pass_context
def test_connection(ctx: click.Context) -> None:
    """Test GitHub API connection and authentication.

    Tests the GitHub API connection using configured credentials.
    """
    log_operation_start("github connection test")

    try:
        # Load configuration
        config_manager = ConfigManager()
        config_manager.load_config()

        # Get GitHub credentials
        credentials = config_manager.get_github_credentials()

        # Create GitHub client
        github_client = create_github_client(
            token=(
                credentials["token"] if isinstance(credentials["token"], str) else None
            ),
            username=(
                credentials["username"]
                if isinstance(credentials["username"], str)
                else None
            ),
            password=(
                credentials["password"]
                if isinstance(credentials["password"], str)
                else None
            ),
            base_url=(
                str(credentials["base_url"])
                if credentials["base_url"]
                else "https://api.github.com"
            ),
        )

        # Test connection
        if github_client.test_connection():
            log_operation_success("github connection test")
            print_success_panel(
                "GitHub API Connection Successful!",
                "✅ Successfully connected to GitHub API\n\n"
                "Authentication: Working\n"
                "Rate Limits: Available\n"
                "API Endpoint: Ready",
            )

            # Show rate limit status
            try:
                rate_limit = github_client.get_rate_limit_status()
                print_info_panel(
                    "Rate Limit Status",
                    f"Core API: {rate_limit['core']['remaining']}/{rate_limit['core']['limit']} remaining\n"
                    f"Search API: {rate_limit['search']['remaining']}/{rate_limit['search']['limit']} remaining",
                )
            except Exception as e:
                print_warning_panel(
                    "Rate Limit Info",
                    f"Could not retrieve rate limit information: {e}",
                )
        else:
            log_operation_failure(
                "github connection test", Exception("Connection test failed")
            )
            print_error_panel(
                "GitHub API Connection Failed",
                "❌ Failed to connect to GitHub API\n\n"
                "Please check:\n"
                "1. Your GitHub credentials (GITHUB_TOKEN or GITHUB_USERNAME/GITHUB_PASSWORD)\n"
                "2. Network connectivity\n"
                "3. GitHub API status",
            )
            sys.exit(1)

    except Exception as e:
        log_operation_failure("github connection test", e)
        print_error_panel("Error testing GitHub connection", str(e))
        sys.exit(1)


@github.command()
@click.option("--repo", "-r", required=True, help="Repository name (owner/repo)")
@click.pass_context
def get_repo(ctx: click.Context, repo: str) -> None:
    """Get repository information from GitHub.

    Fetches detailed information about a GitHub repository.
    """
    log_operation_start("github repository fetch", repo=repo)

    try:
        # Load configuration
        config_manager = ConfigManager()
        config_manager.load_config()

        # Get GitHub credentials
        credentials = config_manager.get_github_credentials()

        # Create GitHub client
        github_client = create_github_client(
            token=(
                credentials["token"] if isinstance(credentials["token"], str) else None
            ),
            username=(
                credentials["username"]
                if isinstance(credentials["username"], str)
                else None
            ),
            password=(
                credentials["password"]
                if isinstance(credentials["password"], str)
                else None
            ),
            base_url=(
                str(credentials["base_url"])
                if credentials["base_url"]
                else "https://api.github.com"
            ),
        )

        # Get repository information
        github_repo = github_client.get_repository(repo)

        if github_repo:
            log_operation_success("github repository fetch", repo=repo)
            print_success_panel(
                f"Repository: {github_repo.name}",
                f"📁 {github_repo.full_name}\n\n"
                f"Description: {github_repo.description or 'No description'}\n"
                f"Language: {github_repo.language or 'Unknown'}\n"
                f"Stars: {github_repo.stargazers_count}\n"
                f"Forks: {github_repo.forks_count}\n"
                f"Open Issues: {github_repo.open_issues_count}\n"
                f"Default Branch: {github_repo.default_branch}\n"
                f"Last Updated: {github_repo.updated_at}\n"
                f"URL: {github_repo.html_url}",
            )

            if github_repo.topics:
                print_info_panel(
                    "Topics",
                    f"Topics: {', '.join(github_repo.topics)}",
                )
        else:
            log_operation_failure(
                "github repository fetch", Exception("Repository not found")
            )
            print_error_panel(
                "Repository Not Found",
                f"❌ Repository '{repo}' not found or not accessible",
            )
            sys.exit(1)

    except Exception as e:
        log_operation_failure("github repository fetch", e)
        print_error_panel("Error fetching repository", str(e))
        sys.exit(1)


@github.command()
@click.option("--repo", "-r", required=True, help="Repository name (owner/repo)")
@click.option("--state", "-s", default="open", help="Issue state (open, closed, all)")
@click.option("--labels", "-l", help="Filter by labels (comma-separated)")
@click.option("--exclude-labels", "-e", help="Exclude labels (comma-separated)")
@click.option("--assignee", "-a", help="Filter by assignee")
@click.option("--milestone", "-m", help="Filter by milestone")
@click.option("--limit", "-n", type=int, help="Maximum number of issues")
@click.option("--created-after", help="Filter issues created after date (YYYY-MM-DD)")
@click.option("--updated-after", help="Filter issues updated after date (YYYY-MM-DD)")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed issue information")
@click.pass_context
def get_issues(
    ctx: click.Context,
    repo: str,
    state: str,
    labels: Optional[str],
    exclude_labels: Optional[str],
    assignee: Optional[str],
    milestone: Optional[str],
    limit: Optional[int],
    created_after: Optional[str],
    updated_after: Optional[str],
    detailed: bool,
) -> None:
    """Get issues from a GitHub repository with advanced filtering.

    Fetches issues from the specified repository with comprehensive filtering options.
    """
    log_operation_start("github issues fetch", repo=repo, state=state)

    try:
        # Load configuration
        config_manager = ConfigManager()
        config_manager.load_config()

        # Get GitHub credentials
        credentials = config_manager.get_github_credentials()

        # Create GitHub client
        github_client = create_github_client(
            token=(
                credentials["token"] if isinstance(credentials["token"], str) else None
            ),
            username=(
                credentials["username"]
                if isinstance(credentials["username"], str)
                else None
            ),
            password=(
                credentials["password"]
                if isinstance(credentials["password"], str)
                else None
            ),
            base_url=(
                str(credentials["base_url"])
                if credentials["base_url"]
                else "https://api.github.com"
            ),
        )

        # Parse labels
        label_list = None
        if labels:
            label_list = [label.strip() for label in labels.split(",")]

        exclude_label_list = None
        if exclude_labels:
            exclude_label_list = [label.strip() for label in exclude_labels.split(",")]

        # Get issues
        issues = github_client.get_issues(
            repo_name=repo,
            state=state,
            labels=label_list,
            exclude_labels=exclude_label_list,
            assignee=assignee,
            milestone=milestone,
            limit=limit,
            created_after=created_after,
            updated_after=updated_after,
        )

        log_operation_success("github issues fetch", repo=repo, count=len(issues))
        print_success_panel(
            f"Found {len(issues)} issues",
            f"📋 Found {len(issues)} issues in {repo}",
        )

        for issue in issues:
            if detailed:
                print_info_panel(
                    f"#{issue.number} - {issue.title}",
                    f"State: {issue.state}\n"
                    f"Labels: {', '.join(issue.labels) if issue.labels else 'None'}\n"
                    f"Assignees: {', '.join(issue.assignees) if issue.assignees else 'None'}\n"
                    f"User: {issue.user or 'Unknown'}\n"
                    f"Milestone: {issue.milestone or 'None'}\n"
                    f"Comments: {issue.comments_count}\n"
                    f"Reactions: {issue.reactions_count}\n"
                    f"Created: {issue.created_at}\n"
                    f"Updated: {issue.updated_at}\n"
                    f"URL: {issue.html_url}",
                )
            else:
                print_info_panel(
                    f"#{issue.number} - {issue.title}",
                    f"State: {issue.state}\n"
                    f"Labels: {', '.join(issue.labels) if issue.labels else 'None'}\n"
                    f"Created: {issue.created_at}\n"
                    f"Updated: {issue.updated_at}\n"
                    f"URL: {issue.html_url}",
                )

    except Exception as e:
        log_operation_failure("github issues fetch", e)
        print_error_panel("Error fetching issues", str(e))
        sys.exit(1)


@github.command()
@click.option("--repos", "-r", required=True, help="Repository names (comma-separated)")
@click.option("--state", "-s", default="open", help="Issue state (open, closed, all)")
@click.option("--labels", "-l", help="Filter by labels (comma-separated)")
@click.option("--exclude-labels", "-e", help="Exclude labels (comma-separated)")
@click.option("--assignee", "-a", help="Filter by assignee")
@click.option("--milestone", "-m", help="Filter by milestone")
@click.option("--limit-per-repo", type=int, help="Maximum issues per repository")
@click.option(
    "--total-limit", type=int, help="Maximum total issues across all repositories"
)
@click.option("--created-after", help="Filter issues created after date (YYYY-MM-DD)")
@click.option("--updated-after", help="Filter issues updated after date (YYYY-MM-DD)")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed issue information")
@click.option("--export", help="Export results to JSON file")
@click.pass_context
def get_issues_multi(
    ctx: click.Context,
    repos: str,
    state: str,
    labels: Optional[str],
    exclude_labels: Optional[str],
    assignee: Optional[str],
    milestone: Optional[str],
    limit_per_repo: Optional[int],
    total_limit: Optional[int],
    created_after: Optional[str],
    updated_after: Optional[str],
    detailed: bool,
    export: Optional[str],
) -> None:
    """Get issues from multiple GitHub repositories with advanced filtering.

    Fetches issues from multiple repositories with comprehensive filtering options.
    """
    log_operation_start("github issues fetch multiple repos", repos=repos, state=state)

    try:
        # Load configuration
        config_manager = ConfigManager()
        config_manager.load_config()

        # Get GitHub credentials
        credentials = config_manager.get_github_credentials()

        # Create GitHub client
        github_client = create_github_client(
            token=(
                credentials["token"] if isinstance(credentials["token"], str) else None
            ),
            username=(
                credentials["username"]
                if isinstance(credentials["username"], str)
                else None
            ),
            password=(
                credentials["password"]
                if isinstance(credentials["password"], str)
                else None
            ),
            base_url=(
                str(credentials["base_url"])
                if credentials["base_url"]
                else "https://api.github.com"
            ),
        )

        # Parse repository list
        repo_list = [repo.strip() for repo in repos.split(",")]

        # Parse labels
        label_list = None
        if labels:
            label_list = [label.strip() for label in labels.split(",")]

        exclude_label_list = None
        if exclude_labels:
            exclude_label_list = [label.strip() for label in exclude_labels.split(",")]

        # Get issues from multiple repositories
        all_issues = github_client.get_issues_for_repositories(
            repositories=repo_list,
            state=state,
            labels=label_list,
            exclude_labels=exclude_label_list,
            assignee=assignee,
            milestone=milestone,
            limit_per_repo=limit_per_repo,
            total_limit=total_limit,
            created_after=created_after,
            updated_after=updated_after,
        )

        total_issues = sum(len(issues) for issues in all_issues.values())
        log_operation_success(
            "github issues fetch multiple repos", repos=repos, total_count=total_issues
        )
        print_success_panel(
            f"Found {total_issues} issues across {len(repo_list)} repositories",
            f"📋 Found {total_issues} issues across {len(repo_list)} repositories",
        )

        # Display results by repository
        for repo_name, issues in all_issues.items():
            if issues:
                print_info_panel(
                    f"Repository: {repo_name}",
                    f"Found {len(issues)} issues",
                )

                for issue in issues:
                    if detailed:
                        print_info_panel(
                            f"#{issue.number} - {issue.title}",
                            f"Repository: {repo_name}\n"
                            f"State: {issue.state}\n"
                            f"Labels: {', '.join(issue.labels) if issue.labels else 'None'}\n"
                            f"Assignees: {', '.join(issue.assignees) if issue.assignees else 'None'}\n"
                            f"User: {issue.user or 'Unknown'}\n"
                            f"Milestone: {issue.milestone or 'None'}\n"
                            f"Comments: {issue.comments_count}\n"
                            f"Reactions: {issue.reactions_count}\n"
                            f"Created: {issue.created_at}\n"
                            f"Updated: {issue.updated_at}\n"
                            f"URL: {issue.html_url}",
                        )
                    else:
                        print_info_panel(
                            f"#{issue.number} - {issue.title}",
                            f"Repository: {repo_name}\n"
                            f"State: {issue.state}\n"
                            f"Labels: {', '.join(issue.labels) if issue.labels else 'None'}\n"
                            f"Created: {issue.created_at}\n"
                            f"Updated: {issue.updated_at}\n"
                            f"URL: {issue.html_url}",
                        )

        # Export results if requested
        if export:
            try:
                import json
                from datetime import datetime

                export_data = {
                    "exported_at": datetime.now().isoformat(),
                    "filters": {
                        "state": state,
                        "labels": label_list,
                        "exclude_labels": exclude_label_list,
                        "assignee": assignee,
                        "milestone": milestone,
                        "created_after": created_after,
                        "updated_after": updated_after,
                    },
                    "repositories": repo_list,
                    "total_issues": total_issues,
                    "issues_by_repo": {
                        repo: [
                            {
                                "number": issue.number,
                                "title": issue.title,
                                "state": issue.state,
                                "labels": issue.labels,
                                "assignees": issue.assignees,
                                "created_at": issue.created_at,
                                "updated_at": issue.updated_at,
                                "html_url": issue.html_url,
                                "user": issue.user,
                                "milestone": issue.milestone,
                                "comments_count": issue.comments_count,
                                "reactions_count": issue.reactions_count,
                            }
                            for issue in issues
                        ]
                        for repo, issues in all_issues.items()
                    },
                }

                with open(export, "w", encoding="utf-8") as f:
                    json.dump(export_data, f, indent=2, ensure_ascii=False)

                print_success_panel(
                    "Export Successful",
                    f"✅ Results exported to {export}",
                )

            except Exception as e:
                print_error_panel("Export Failed", f"Failed to export results: {e}")

    except Exception as e:
        log_operation_failure("github issues fetch multiple repos", e)
        print_error_panel("Error fetching issues", str(e))
        sys.exit(1)


@main.group()
@click.pass_context
def contributions(ctx: click.Context) -> None:
    """Manage contribution history and tracking."""
    pass


@contributions.command()
@click.option("--username", "-u", required=True, help="GitHub username to sync")
@click.option("--force", "-f", is_flag=True, help="Force sync even if recent")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output")
@click.pass_context
def sync_history(ctx: click.Context, username: str, force: bool, quiet: bool) -> None:
    """Sync contribution history from GitHub."""
    print_info_panel(
        "Syncing Contribution History",
        f"Fetching contributions for user: {username}",
    )

    try:
        # Load configuration
        config_manager = get_config_manager()
        config = config_manager.load_config()

        # Create GitHub client
        github_credentials = config_manager.get_github_credentials()
        github_client = create_github_client(
            token=github_credentials.get("token"),  # type: ignore
            username=github_credentials.get("username"),  # type: ignore
            password=github_credentials.get("password"),  # type: ignore
            base_url=config.settings.github_api_url,
        )

        # Test GitHub connection
        if not github_client.test_connection():
            print_error_panel(
                "GitHub Connection Failed",
                "Unable to connect to GitHub API. Please check your credentials.",
            )
            return

        # Create contribution tracker
        from .contribution_tracker import create_contribution_tracker

        tracker = create_contribution_tracker(config, github_client)

        # Sync contributions
        tracker.sync_contributions_from_github(username)

        print_success_panel(
            "Sync Completed",
            f"✅ Successfully synced contributions for {username}",
        )

    except Exception as e:
        print_error_panel(
            "Sync Failed",
            f"An error occurred during sync: {str(e)}",
        )
        if ctx.obj.get("verbose"):
            raise


@contributions.command()
@click.option("--days", "-d", type=int, help="Show stats for last N days")
@click.option("--export", "-e", help="Export stats to file (.json or .csv)")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output")
@click.pass_context
def stats(
    ctx: click.Context, days: Optional[int], export: Optional[str], quiet: bool
) -> None:
    """Show contribution statistics."""
    print_info_panel(
        "Calculating Contribution Statistics",
        "Analyzing your contribution history...",
    )

    try:
        # Load configuration
        config_manager = get_config_manager()
        config = config_manager.load_config()

        # Create GitHub client
        github_credentials = config_manager.get_github_credentials()
        github_client = create_github_client(
            token=github_credentials.get("token"),  # type: ignore
            username=github_credentials.get("username"),  # type: ignore
            password=github_credentials.get("password"),  # type: ignore
            base_url=config.settings.github_api_url,
        )

        # Create contribution tracker
        from .contribution_tracker import create_contribution_tracker

        tracker = create_contribution_tracker(config, github_client)

        # Get statistics
        stats = tracker.get_contribution_stats(days)

        # Display basic statistics
        print_success_panel(
            "Contribution Statistics",
            f"📊 Total Contributions: {stats.total_contributions}\n"
            f"📈 Open: {stats.open_contributions} | Closed: {stats.closed_contributions} | Merged: {stats.merged_contributions}\n"
            f"🏢 Repositories: {stats.repositories_contributed_to}\n"
            f"💡 Skills Developed: {len(stats.skills_developed)}\n"
            f"⭐ Average Impact Score: {stats.average_impact_score:.2f}",
        )

        # Enhanced impact metrics
        if stats.high_impact_contributions > 0 or stats.critical_contributions > 0:
            impact_summary = f"🔥 High Impact: {stats.high_impact_contributions}"
            if stats.critical_contributions > 0:
                impact_summary += f" | 🚀 Critical: {stats.critical_contributions}"
            print_info_panel("Impact Metrics", impact_summary)

        # Trending analysis
        if stats.contribution_velocity > 0:
            velocity_trend = "📈" if stats.contribution_velocity > 0.1 else "📊"
            print_info_panel(
                "Contribution Velocity",
                f"{velocity_trend} {stats.contribution_velocity:.2f} contributions/day (30d)",
            )

        # Impact trends
        if stats.impact_trend_30d != 0 or stats.impact_trend_7d != 0:
            trend_summary = ""
            if stats.impact_trend_30d != 0:
                trend_icon = "📈" if stats.impact_trend_30d > 0 else "📉"
                trend_summary += f"{trend_icon} 30d: {stats.impact_trend_30d:+.2f} "
            if stats.impact_trend_7d != 0:
                trend_icon = "📈" if stats.impact_trend_7d > 0 else "📉"
                trend_summary += f"{trend_icon} 7d: {stats.impact_trend_7d:+.2f}"
            print_info_panel("Impact Trends", trend_summary)

        # Trending skills
        if stats.trending_skills:
            trending_list = ", ".join(stats.trending_skills[:5])  # Top 5
            print_info_panel(
                "🚀 Trending Skills",
                f"Skills with growing usage: {trending_list}",
            )

        if stats.declining_skills:
            declining_list = ", ".join(stats.declining_skills[:5])  # Top 5
            print_info_panel(
                "📉 Declining Skills",
                f"Skills with declining usage: {declining_list}",
            )

        # Advanced metrics
        if stats.collaboration_score > 0 or stats.recognition_score > 0:
            advanced_summary = ""
            if stats.collaboration_score > 0:
                advanced_summary += f"🤝 Collaboration: {stats.collaboration_score:.2f} "
            if stats.recognition_score > 0:
                advanced_summary += f"🏆 Recognition: {stats.recognition_score:.2f} "
            if stats.influence_score > 0:
                advanced_summary += f"💪 Influence: {stats.influence_score:.2f} "
            if stats.sustainability_score > 0:
                advanced_summary += (
                    f"🌱 Sustainability: {stats.sustainability_score:.2f}"
                )
            print_info_panel("Advanced Metrics", advanced_summary)

        # Show skills
        if stats.skills_developed:
            skills_list = ", ".join(sorted(stats.skills_developed))
            print_info_panel(
                "Skills Developed",
                f"🎯 {skills_list}",
            )

        # Skill impact scores
        if stats.skill_impact_scores:
            top_skills = sorted(
                stats.skill_impact_scores.items(), key=lambda x: x[1], reverse=True
            )[
                :3
            ]  # Top 3
            skill_impact_summary = ""
            for skill, impact in top_skills:
                skill_impact_summary += f"{skill}: {impact:.2f} "
            print_info_panel("Top Skill Impact", skill_impact_summary)

        # Repository impact scores
        if stats.repository_impact_scores:
            top_repos = sorted(
                stats.repository_impact_scores.items(), key=lambda x: x[1], reverse=True
            )[
                :3
            ]  # Top 3
            repo_impact_summary = ""
            for repo, impact in top_repos:
                repo_name = repo.split("/")[-1]  # Just the repo name
                repo_impact_summary += f"{repo_name}: {impact:.2f} "
            print_info_panel("Top Repository Impact", repo_impact_summary)

        # Show recent activity
        if stats.recent_activity:
            print_info_panel(
                "Recent Activity",
                f"🕒 Last {len(stats.recent_activity)} contributions:",
            )
            for i, contribution in enumerate(stats.recent_activity[:5], 1):
                print_info_panel(
                    f"{i}. {contribution.issue_title}",
                    f"Repository: {contribution.repository}\n"
                    f"Type: {contribution.contribution_type}\n"
                    f"Status: {contribution.status}\n"
                    f"Impact: {contribution.impact_score:.2f}\n"
                    f"Skills: {', '.join(contribution.skills_used)}",
                )

        # Export if requested
        if export:
            try:
                from pathlib import Path

                # Determine export format based on file extension
                export_path = Path(export)
                is_csv_export = export_path.suffix.lower() == ".csv"

                if is_csv_export:
                    # Get all contributions for CSV export
                    all_contributions = tracker.load_contribution_history()

                    # Filter by days if specified
                    if days:
                        from datetime import datetime, timedelta

                        cutoff_date = datetime.now() - timedelta(days=days)
                        all_contributions = [
                            c
                            for c in all_contributions
                            if datetime.fromisoformat(c.created_at) >= cutoff_date
                        ]

                    # Export to CSV
                    export_contribution_data_to_csv(all_contributions, export)
                else:
                    # JSON export (existing functionality)
                    import json
                    from datetime import datetime

                    export_data = {
                        "exported_at": datetime.now().isoformat(),
                        "period_days": days,
                        "statistics": {
                            "total_contributions": stats.total_contributions,
                            "open_contributions": stats.open_contributions,
                            "closed_contributions": stats.closed_contributions,
                            "merged_contributions": stats.merged_contributions,
                            "repositories_contributed_to": stats.repositories_contributed_to,
                            "skills_developed": list(stats.skills_developed),
                            "total_impact_score": stats.total_impact_score,
                            "average_impact_score": stats.average_impact_score,
                            "contribution_timeline": stats.contribution_timeline,
                            # Enhanced impact metrics
                            "high_impact_contributions": stats.high_impact_contributions,
                            "critical_contributions": stats.critical_contributions,
                            "impact_trend_30d": stats.impact_trend_30d,
                            "impact_trend_7d": stats.impact_trend_7d,
                            # Trending analysis
                            "contribution_velocity": stats.contribution_velocity,
                            "trending_skills": stats.trending_skills,
                            "declining_skills": stats.declining_skills,
                            "skill_growth_rate": stats.skill_growth_rate,
                            "repository_engagement_trend": stats.repository_engagement_trend,
                            # Advanced metrics
                            "collaboration_score": stats.collaboration_score,
                            "recognition_score": stats.recognition_score,
                            "influence_score": stats.influence_score,
                            "sustainability_score": stats.sustainability_score,
                            # Impact scores by category
                            "skill_impact_scores": stats.skill_impact_scores,
                            "repository_impact_scores": stats.repository_impact_scores,
                        },
                        "recent_activity": [
                            {
                                "repository": c.repository,
                                "issue_number": c.issue_number,
                                "issue_title": c.issue_title,
                                "contribution_type": c.contribution_type,
                                "status": c.status,
                                "impact_score": c.impact_score,
                                "skills_used": c.skills_used,
                                "created_at": c.created_at,
                                "updated_at": c.updated_at,
                            }
                            for c in stats.recent_activity
                        ],
                    }

                    with open(export, "w", encoding="utf-8") as f:
                        json.dump(export_data, f, indent=2, ensure_ascii=False)

                    print_success_panel(
                        "Export Successful",
                        f"✅ Statistics exported to {export}",
                    )

            except Exception as e:
                print_error_panel("Export Failed", f"Failed to export statistics: {e}")

    except Exception as e:
        print_error_panel(
            "Statistics Failed",
            f"An error occurred while calculating statistics: {str(e)}",
        )
        if ctx.obj.get("verbose"):
            raise


@contributions.command()
@click.option("--skill", "-s", help="Filter by skill")
@click.option("--repository", "-r", help="Filter by repository")
@click.option("--limit", "-n", type=int, default=10, help="Number of recommendations")
@click.pass_context
def recommendations(
    ctx: click.Context, skill: Optional[str], repository: Optional[str], limit: int
) -> None:
    """Show contribution recommendations based on history."""
    print_info_panel(
        "Generating Recommendations",
        "Analyzing your contribution history for personalized recommendations...",
    )

    try:
        # Load configuration
        config_manager = get_config_manager()
        config = config_manager.load_config()

        # Create GitHub client
        github_credentials = config_manager.get_github_credentials()
        github_client = create_github_client(
            token=github_credentials.get("token"),  # type: ignore
            username=github_credentials.get("username"),  # type: ignore
            password=github_credentials.get("password"),  # type: ignore
            base_url=config.settings.github_api_url,
        )

        # Create contribution tracker
        from .contribution_tracker import create_contribution_tracker

        tracker = create_contribution_tracker(config, github_client)

        # Get user skills from contributions
        stats = tracker.get_contribution_stats()
        user_skills = list(stats.skills_developed)

        if not user_skills:
            print_warning_panel(
                "No Skills Found",
                "No skills detected in your contribution history. "
                "Try syncing your contributions first with 'gitco contributions sync-history'.",
            )
            return

        # Get recommendations
        recommendations = tracker.get_contribution_recommendations(user_skills)

        # Filter by skill if specified
        if skill:
            recommendations = [
                r
                for r in recommendations
                if skill.lower() in [s.lower() for s in r.skills_used]
            ]

        # Filter by repository if specified
        if repository:
            recommendations = [
                r for r in recommendations if repository.lower() in r.repository.lower()
            ]

        # Limit results
        recommendations = recommendations[:limit]

        if not recommendations:
            print_warning_panel(
                "No Recommendations",
                "No recommendations found with the current filters.",
            )
            return

        print_success_panel(
            "Contribution Recommendations",
            f"Found {len(recommendations)} recommendations based on your skills: {', '.join(user_skills)}",
        )

        for i, recommendation in enumerate(recommendations, 1):
            print_info_panel(
                f"{i}. {recommendation.issue_title}",
                f"Repository: {recommendation.repository}\n"
                f"Type: {recommendation.contribution_type}\n"
                f"Status: {recommendation.status}\n"
                f"Impact: {recommendation.impact_score:.2f}\n"
                f"Skills: {', '.join(recommendation.skills_used)}",
            )

    except Exception as e:
        print_error_panel(
            "Recommendations Failed",
            f"An error occurred while generating recommendations: {str(e)}",
        )
        if ctx.obj.get("verbose"):
            raise


@contributions.command()
@click.option("--days", "-d", type=int, help="Export contributions from last N days")
@click.option("--output", "-o", required=True, help="Output file path (.csv or .json)")
@click.option("--include-stats", "-s", is_flag=True, help="Include summary statistics")
@click.pass_context
def export(
    ctx: click.Context, days: Optional[int], output: str, include_stats: bool
) -> None:
    """Export contribution data to CSV or JSON format."""
    print_info_panel(
        "Exporting Contribution Data",
        f"Preparing contribution data for export to {output}...",
    )

    try:
        # Load configuration
        config_manager = get_config_manager()
        config = config_manager.load_config()

        # Create GitHub client
        github_credentials = config_manager.get_github_credentials()
        github_client = create_github_client(
            token=github_credentials.get("token"),  # type: ignore
            username=github_credentials.get("username"),  # type: ignore
            password=github_credentials.get("password"),  # type: ignore
            base_url=config.settings.github_api_url,
        )

        # Create contribution tracker
        from .contribution_tracker import create_contribution_tracker

        tracker = create_contribution_tracker(config, github_client)

        # Get all contributions
        all_contributions = tracker.load_contribution_history()

        # Filter by days if specified
        if days:
            from datetime import datetime, timedelta

            cutoff_date = datetime.now() - timedelta(days=days)
            all_contributions = [
                c
                for c in all_contributions
                if datetime.fromisoformat(c.created_at) >= cutoff_date
            ]

        if not all_contributions:
            print_warning_panel(
                "No Contributions Found",
                "No contributions found for the specified period.",
            )
            return

        # Determine export format based on file extension
        from pathlib import Path

        export_path = Path(output)
        is_csv_export = export_path.suffix.lower() == ".csv"

        if is_csv_export:
            # Export to CSV
            export_contribution_data_to_csv(all_contributions, output, include_stats)
        else:
            # Export to JSON
            import json
            from datetime import datetime

            export_data = {
                "exported_at": datetime.now().isoformat(),
                "period_days": days,
                "total_contributions": len(all_contributions),
                "contributions": [
                    {
                        "repository": c.repository,
                        "issue_number": c.issue_number,
                        "issue_title": c.issue_title,
                        "issue_url": c.issue_url,
                        "contribution_type": c.contribution_type,
                        "status": c.status,
                        "created_at": c.created_at,
                        "updated_at": c.updated_at,
                        "skills_used": c.skills_used,
                        "impact_score": c.impact_score,
                        "labels": c.labels,
                        "milestone": c.milestone,
                        "assignees": c.assignees,
                        "comments_count": c.comments_count,
                        "reactions_count": c.reactions_count,
                    }
                    for c in all_contributions
                ],
            }

            with open(output, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            print_success_panel(
                "Export Successful",
                f"✅ Contribution data exported to {output}",
            )

    except Exception as e:
        print_error_panel(
            "Export Failed",
            f"An error occurred while exporting contribution data: {str(e)}",
        )
        if ctx.obj.get("verbose"):
            raise


@contributions.command()
@click.option("--days", "-d", type=int, default=30, help="Analysis period in days")
@click.option("--export", "-e", help="Export trending analysis to file (.json or .csv)")
@click.pass_context
def trending(ctx: click.Context, days: Optional[int], export: Optional[str]) -> None:
    """Show detailed trending analysis of your contributions."""
    print_info_panel(
        "Analyzing Contribution Trends",
        f"Calculating trending analysis for the last {days} days...",
    )

    try:
        # Load configuration
        config_manager = get_config_manager()
        config = config_manager.load_config()

        # Create GitHub client
        github_credentials = config_manager.get_github_credentials()
        github_client = create_github_client(
            token=github_credentials.get("token"),  # type: ignore
            username=github_credentials.get("username"),  # type: ignore
            password=github_credentials.get("password"),  # type: ignore
            base_url=config.settings.github_api_url,
        )

        # Create contribution tracker
        from .contribution_tracker import create_contribution_tracker

        tracker = create_contribution_tracker(config, github_client)

        # Get statistics with enhanced metrics
        stats = tracker.get_contribution_stats(days)

        print_success_panel(
            "Trending Analysis",
            f"📊 Analysis period: {days} days\n"
            f"🚀 Contribution velocity: {stats.contribution_velocity:.2f} contributions/day",
        )

        # Impact trends
        if stats.impact_trend_30d != 0 or stats.impact_trend_7d != 0:
            trend_summary = ""
            if stats.impact_trend_30d != 0:
                trend_icon = "📈" if stats.impact_trend_30d > 0 else "📉"
                trend_summary += (
                    f"{trend_icon} 30d trend: {stats.impact_trend_30d:+.2f} "
                )
            if stats.impact_trend_7d != 0:
                trend_icon = "📈" if stats.impact_trend_7d > 0 else "📉"
                trend_summary += f"{trend_icon} 7d trend: {stats.impact_trend_7d:+.2f}"
            print_info_panel("Impact Trends", trend_summary)

        # Skill growth analysis
        if stats.skill_growth_rate:
            growing_skills = [
                skill for skill, rate in stats.skill_growth_rate.items() if rate > 0.2
            ]
            declining_skills = [
                skill for skill, rate in stats.skill_growth_rate.items() if rate < -0.2
            ]

            if growing_skills:
                print_info_panel(
                    "🚀 Fastest Growing Skills",
                    f"Skills with >20% growth: {', '.join(growing_skills[:5])}",
                )

            if declining_skills:
                print_info_panel(
                    "📉 Declining Skills",
                    f"Skills with declining usage: {', '.join(declining_skills[:5])}",
                )

        # Repository engagement trends
        if stats.repository_engagement_trend:
            top_engaged = sorted(
                stats.repository_engagement_trend.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3]

            if top_engaged:
                engagement_summary = ""
                for repo, trend in top_engaged:
                    repo_name = repo.split("/")[-1]
                    trend_icon = "📈" if trend > 0 else "📉"
                    engagement_summary += f"{repo_name}: {trend_icon}{trend:+.1f} "
                print_info_panel("Top Repository Engagement", engagement_summary)

        # Advanced metrics breakdown
        if stats.collaboration_score > 0 or stats.recognition_score > 0:
            metrics_summary = ""
            if stats.collaboration_score > 0:
                metrics_summary += f"🤝 Collaboration: {stats.collaboration_score:.2f} "
            if stats.recognition_score > 0:
                metrics_summary += f"🏆 Recognition: {stats.recognition_score:.2f} "
            if stats.influence_score > 0:
                metrics_summary += f"💪 Influence: {stats.influence_score:.2f} "
            if stats.sustainability_score > 0:
                metrics_summary += f"🌱 Sustainability: {stats.sustainability_score:.2f}"
            print_info_panel("Advanced Metrics", metrics_summary)

        # Skill impact analysis
        if stats.skill_impact_scores:
            top_impact_skills = sorted(
                stats.skill_impact_scores.items(), key=lambda x: x[1], reverse=True
            )[:5]

            impact_summary = ""
            for skill, impact in top_impact_skills:
                impact_summary += f"{skill}: {impact:.2f} "
            print_info_panel("Highest Impact Skills", impact_summary)

        # Repository impact analysis
        if stats.repository_impact_scores:
            top_impact_repos = sorted(
                stats.repository_impact_scores.items(), key=lambda x: x[1], reverse=True
            )[:3]

            repo_impact_summary = ""
            for repo, impact in top_impact_repos:
                repo_name = repo.split("/")[-1]
                repo_impact_summary += f"{repo_name}: {impact:.2f} "
            print_info_panel("Highest Impact Repositories", repo_impact_summary)

        # Export if requested
        if export:
            try:
                from pathlib import Path

                # Determine export format based on file extension
                export_path = Path(export)
                is_csv_export = export_path.suffix.lower() == ".csv"

                if is_csv_export:
                    # Get all contributions for CSV export
                    all_contributions = tracker.load_contribution_history()

                    # Filter by days if specified
                    if days:
                        from datetime import datetime, timedelta

                        cutoff_date = datetime.now() - timedelta(days=days)
                        all_contributions = [
                            c
                            for c in all_contributions
                            if datetime.fromisoformat(c.created_at) >= cutoff_date
                        ]

                    # Export to CSV
                    export_contribution_data_to_csv(all_contributions, export)
                else:
                    # JSON export (existing functionality)
                    import json
                    from datetime import datetime

                    export_data = {
                        "exported_at": datetime.now().isoformat(),
                        "analysis_period_days": days,
                        "trending_analysis": {
                            "contribution_velocity": stats.contribution_velocity,
                            "impact_trend_30d": stats.impact_trend_30d,
                            "impact_trend_7d": stats.impact_trend_7d,
                            "trending_skills": stats.trending_skills,
                            "declining_skills": stats.declining_skills,
                            "skill_growth_rate": stats.skill_growth_rate,
                            "repository_engagement_trend": stats.repository_engagement_trend,
                            "collaboration_score": stats.collaboration_score,
                            "recognition_score": stats.recognition_score,
                            "influence_score": stats.influence_score,
                            "sustainability_score": stats.sustainability_score,
                            "skill_impact_scores": stats.skill_impact_scores,
                            "repository_impact_scores": stats.repository_impact_scores,
                        },
                    }

                    with open(export, "w", encoding="utf-8") as f:
                        json.dump(export_data, f, indent=2, ensure_ascii=False)

                    print_success_panel(
                        "Export Successful",
                        f"✅ Trending analysis exported to {export}",
                    )

            except Exception as e:
                print_error_panel(
                    "Export Failed", f"Failed to export trending analysis: {e}"
                )

    except Exception as e:
        print_error_panel(
            "Trending Analysis Failed",
            f"An error occurred while analyzing trends: {str(e)}",
        )
        if ctx.obj.get("verbose"):
            raise


if __name__ == "__main__":
    main()
