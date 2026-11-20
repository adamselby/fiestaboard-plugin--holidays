# Holidays, a FiestaBoard plugin

Celebrate holidays, with several standard ones preloaded but fully customizable. No calendar needed, all details are stored in plugin settings.

## Configure

| Setting | Notes |
|---|---|
| Holidays | One row per holiday: name, date rule, optional message and colors. Rules: `12-25`, `4th thu nov`, `last mon may`, `easter`, `easter-47`. |
| Timezone | Defaults to America/Chicago. |
| Upcoming Window | How far ahead `holidays.upcoming` looks. Default 30 days. |
| Take Over the Board | Takes over on holidays. Off by default. |
| Takeover Priority | Higher number wins. Default 40. |

### Page recipes

Today's holiday message, or which holiday is next:

```text
{{= IF(holidays.is_holiday = "true", holidays.message, "NEXT: " & holidays.next_formatted) }}
```

Supports Vestaboard and Vestaboard Note
