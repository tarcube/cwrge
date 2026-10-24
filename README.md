## Basic Premise or Summary of Event
- University teams capture campus buildings by individual players submitting 
  a score from each node's specific/dedicated rhythm game chart on Discord/GH-Page Webapp.
- Players can only hop from node to node on the path shared by their university team.
- Whichever university team captures the most campus buildings by the end of November wins a prize.
- It's like any other territory takeover game (so JetLagTG challenge coded)

## Link to Github Pages (Webapp)
- https://tarcube.github.io/cwrge/cwrge_map.html

## TODO:
- Add McGill and York campuses (I can't find any building shapefiles)
- Fix McMaster University containing demolished buildings (like Drill Hall)
- Fix UBC not being connected (it's broken into two parts)
- List actual home/host buildings (I used random ones for the demo)
- Poll a list of charts from various rhythm games to actually assign to nodes (and not ai gen ones)
- Refine the rules for powerups/economy
- Adding a dashboard that shows team's stamina, notes, lives, and pending declarations
- Timeline scrubber that can rewind the map to see territory progression day by day
- Leaderboard that ranks teams by buildings controlled, scores submitted, or notes generated
- Set a timeline of events before Halloween to be able to actually host this in November this term.
- Advertise to club members

## Updating territory during the event

1. Open Google Sheet
2. Add rows for newly captured buildings
3. Update existing rows if recaptured
4. Users refresh the map for the changes appear
5. No need to git push because the sheet is the source of truth.