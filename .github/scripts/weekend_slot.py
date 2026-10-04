"""Resolve one DST-correct scheduled phase inside its fixed London window."""
from datetime import datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo
LONDON = ZoneInfo('Europe/London')
DAYS = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}
def scheduled_phase(now, cron, slots):
    now = now.astimezone(LONDON)
    for phase, (start, minutes, workflow) in slots.items():
        if start == 'N/A':continue
        day, clock = start.split(); hour, minute = map(int, clock.split(':'))
        date = now.date() - timedelta(days=(now.weekday()-DAYS[day]) % 7)
        instant = datetime(date.year, date.month, date.day, hour, minute, tzinfo=LONDON)
        utc = instant.astimezone(timezone.utc)
        expected = f'{utc.minute} {utc.hour} * * {(utc.weekday()+1) % 7}'
        if cron == expected and instant <= now < instant+timedelta(minutes=minutes):
            return phase, workflow
    return None

def main():
    slots = {
        'ci': (os.environ['CI_START'], 150, os.environ['CI_WORKFLOW']),
        'dast': (os.environ['DAST_START'], 120, 'dast.yml'),
        'council': (os.environ['COUNCIL_START'], 150, 'council.yml'),
    }
    if os.environ['EVENT_NAME'] == 'workflow_dispatch':
        phase = os.environ.get('MANUAL_PHASE', '')
        if phase not in slots or slots[phase][0] == 'N/A':raise ValueError('Unsupported manual phase')
        selected = phase, slots[phase][2]
    else:
        selected = scheduled_phase(datetime.now(timezone.utc), os.environ['SCHEDULE'], slots)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        if selected:
            phase, workflow = selected
            output.write(f'run=true\nphase={phase}\nworkflow={workflow}\n')
        else:
            output.write('run=false\n')
            print('Other DST cron or outside the fixed phase window; no dispatch.')
if __name__ == '__main__':main()
