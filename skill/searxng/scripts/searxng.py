#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "rich"]
# ///
"""SearXNG CLI - Privacy-respecting metasearch via your local instance."""

import argparse
import os
import sys
import json
import warnings
import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from typing import Optional, List, Dict, Any

# Suppress SSL warnings for local instances
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

console = Console()

def get_searxng_url(url: Optional[str] = None) -> str:
    """Get SearXNG URL from argument, environment variable, or default."""
    if url:
        return url.rstrip('/')
    
    env_url = os.environ.get('SEARXNG_URL')
    if env_url:
        return env_url.rstrip('/')
    
    # Try common local ports
    default_ports = [8888, 8080, 4000, 80]
    for port in default_ports:
        try:
            test_url = f"http://localhost:{port}"
            response = httpx.get(f"{test_url}/healthz", timeout=2, verify=False)
            if response.status_code == 200:
                console.print(f"[green]Found SearXNG at {test_url}[/green]")
                return test_url
        except (httpx.RequestError, httpx.TimeoutException):
            continue
    
    console.print("[red]Error: SearXNG instance not found. Set SEARXNG_URL or use --url[/red]")
    console.print("[yellow]Example: export SEARXNG_URL='http://localhost:8888'[/yellow]")
    sys.exit(1)

def search(
    query: str,
    url: str,
    categories: Optional[str] = None,
    language: Optional[str] = None,
    time_range: Optional[str] = None,
    num_results: int = 10,
    format_output: str = "table"
) -> List[Dict[str, Any]]:
    """Perform a search using SearXNG API."""
    api_url = f"{url}/search"
    
    params = {
        "q": query,
        "format": "json",
        "pageno": 1,
    }
    
    if categories:
        params["categories"] = categories
    if language:
        params["language"] = language
    if time_range:
        params["time_range"] = time_range
    
    try:
        with httpx.Client(verify=False, timeout=30) as client:
            response = client.get(api_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            results = data.get("results", [])[:num_results]
            
            if format_output == "json":
                return results
            
            # Display results
            if not results:
                console.print("[yellow]No results found.[/yellow]")
                return []
            
            table = Table(title=f"Search Results: {query}", show_lines=True)
            table.add_column("#", style="cyan", width=4)
            table.add_column("Title", style="green")
            table.add_column("URL", style="blue")
            table.add_column("Content", style="white")
            
            for idx, result in enumerate(results, 1):
                title = result.get("title", "No title")
                url = result.get("url", "")
                content = result.get("content", "")[:200] + "..." if len(result.get("content", "")) > 200 else result.get("content", "")
                
                table.add_row(str(idx), title, url, content)
            
            console.print(table)
            
            # Show metadata
            number_of_results = data.get("number_of_results", 0)
            console.print(f"\n[dim]Found {number_of_results} total results, showing {len(results)}[/dim]")
            
            return results
            
    except httpx.RequestError as e:
        console.print(f"[red]Error connecting to SearXNG: {e}[/red]")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        console.print(f"[red]HTTP error: {e.response.status_code}[/red]")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description="SearXNG CLI - Privacy-respecting metasearch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s search "Python programming"
  %(prog)s search "latest news" --category news
  %(prog)s search "images of cats" --category images
  %(prog)s search "AI research" --language en --time-range week
  %(prog)s search "query" --format json -n 20
        """
    )
    
    parser.add_argument("--url", "-u", help="SearXNG instance URL")
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Search command
    search_parser = subparsers.add_parser("search", help="Perform a search")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--category", "-c", help="Search category (general, images, news, videos, files, it, science, social media)")
    search_parser.add_argument("--language", "-l", help="Search language (e.g., en, zh, de)")
    search_parser.add_argument("--time-range", "-t", choices=["day", "week", "month", "year"], help="Time range filter")
    search_parser.add_argument("--number", "-n", type=int, default=10, help="Number of results to return (default: 10)")
    search_parser.add_argument("--format", "-f", choices=["table", "json"], default="table", help="Output format (default: table)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    if args.command == "search":
        base_url = get_searxng_url(args.url)
        results = search(
            query=args.query,
            url=base_url,
            categories=args.category,
            language=args.language,
            time_range=args.time_range,
            num_results=args.number,
            format_output=args.format
        )
        
        if args.format == "json":
            print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()