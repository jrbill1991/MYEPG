# EPGShare Clean Merge

This package creates one XMLTV `.xml.gz` feed from these EPGShare sources:

- Brazil: `BR1` and `BR2`
- United States: `US2`
- US Locals: `US_LOCALS1`
- US Sports: `US_SPORTS1`
- United Kingdom: `UK1`
- Canada: `CA2`
- Portugal: `PT1`
- Germany: `DE1`
- France: `FR1`

It also:
- normalizes recoverable malformed 14-digit XMLTV clock values (for example
  `20260807006000 +0000` becomes `20260807010000 +0000`);
- drops programme records whose timestamps cannot safely be interpreted;
- removes exact duplicate programme records;
- keeps only one copy of duplicated channel definitions;
- republishes the latest result automatically.

## Free setup with GitHub

1. Create a new **public GitHub repository**.
2. Upload the contents of this ZIP to the repository root. Make sure the
   `.github/workflows/update-epg.yml` path is preserved.
3. Open the repository's **Actions** tab and allow workflows if GitHub asks.
4. Open the workflow named **Update clean EPG** and choose **Run workflow** once.
5. When it finishes, the repository will have an `output` branch containing
   `guide.xml.gz`.
6. Your permanent raw EPG address will have this form:

   `https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/YOUR_REPOSITORY/output/guide.xml.gz`

   Replace `YOUR_GITHUB_USERNAME` and `YOUR_REPOSITORY` with your actual names.
7. Put that raw URL into IPTV Player Zero as the EPG URL.

The workflow then runs automatically twice per day. Your Windows PC does not
need to stay on.

## Why the output branch is force-replaced

US Locals is very large. Committing a new ~large guide into normal Git history
every day would make the repository grow rapidly. The workflow recreates the
`output` branch as an orphan branch each time, so only the current generated
guide is retained there.

## Important size note

GitHub's normal single-file Git limit is 100 MiB. At the current EPGShare sizes,
these selected feeds should normally remain below that after merging and gzip
compression, but the script deliberately fails if the generated file reaches
100 MiB. If EPGShare grows beyond that point, the hosting method should be
changed rather than publishing an oversized Git blob.

## Run locally for testing

With Python 3.11+ installed:

    python merge_epg.py

The result is:

    guide.xml.gz
