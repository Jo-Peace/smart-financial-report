import argparse
import datetime

from modules.analytics_store import AnalyticsStore


def _optional_float(value):
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(value):
    if value in (None, ""):
        return None
    return int(value)


def main():
    parser = argparse.ArgumentParser(
        description="Record manual YouTube title/thumbnail performance metrics into data/analytics.db."
    )
    parser.add_argument("--date", required=True, help="Video/report date, e.g. 2026-04-28")
    parser.add_argument("--views", type=int, required=True, help="Current view count")
    parser.add_argument("--video-id", default="", help="YouTube video ID")
    parser.add_argument("--hours", type=float, default=None, help="Hours since publish")
    parser.add_argument("--likes", type=int, default=None)
    parser.add_argument("--comments", type=int, default=None)
    parser.add_argument("--impressions", type=int, default=None)
    parser.add_argument("--ctr", type=float, default=None, help="CTR percentage, e.g. 5.4")
    parser.add_argument("--avg-view-duration", type=float, default=None, help="Average view duration in seconds")
    parser.add_argument("--avg-view-percentage", type=float, default=None)
    parser.add_argument("--title-variant", default="", help="A/B")
    parser.add_argument("--thumbnail-variant", default="", help="A/B")
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--measured-at",
        default=datetime.datetime.now().replace(microsecond=0).isoformat(sep=" "),
        help="Measurement timestamp, default now",
    )
    args = parser.parse_args()

    summary = AnalyticsStore().record_youtube_metric(
        run_date=args.date,
        measured_at=args.measured_at,
        video_id=args.video_id,
        hours_since_publish=args.hours,
        views=_optional_int(args.views),
        likes=_optional_int(args.likes),
        comments=_optional_int(args.comments),
        impressions=_optional_int(args.impressions),
        ctr=_optional_float(args.ctr),
        avg_view_duration_seconds=_optional_float(args.avg_view_duration),
        avg_view_percentage=_optional_float(args.avg_view_percentage),
        title_variant=args.title_variant.upper(),
        thumbnail_variant=args.thumbnail_variant.upper(),
        notes=args.notes,
    )
    print(f"Recorded YouTube metric for {args.date}: {args.views:,} views")
    print(f"DB: {summary['db_path']}")
    print(f"YouTube metric samples: {summary['youtube_metric_samples']}")


if __name__ == "__main__":
    main()
