# message.json

Friend message integration — displays ad-hoc messages from registered friends via
webhook. Each message shows a colored header row with the sender's name followed by
the message body. Social — webhook-only, queues normally without interrupting.

Unlike cron-scheduled templates, this template fires only when a webhook event is
received. No content is shown when idle.

## Schedule

**Webhook-only** (no cron). Fires when a registered friend sends a message.
**Hold:** 120 s | **Timeout:** 600 s | **Priority:** 8 (Social)

Messages queue normally (`interrupt=False`) — they do not cut an active hold
short. This means a message arriving during an active Plex session will wait
for the next natural hold break (e.g. playback pause) rather than flapping the
display mid-scene. The 600 s timeout gives messages up to 10 minutes to wait,
which covers typical pause intervals between scenes or episodes.

During normal scheduled content (weather 600 s hold, calendar 300 s hold),
priority 8 puts the message at the front of the queue; it shows as soon as
the current hold completes — usually within a few minutes.

## How it works

The board owner (admin) registers each friend using the **admin Shortcut** (or any
HTTP client). Registration stores a per-friend named credential in `config.toml`
scoped to the `message` webhook. Each friend receives a unique 3-word passphrase
that identifies them automatically — they never configure a name or color themselves.

Friends send messages using the **friend Shortcut**, which prompts for a message and
POSTs it with their passphrase as the webhook secret. The board displays their name
and color automatically.

## Requirements

- The webhook listener enabled in `config.toml` (see below)
- At least one registered friend (see Registration below)

## Configuration

Enable the webhook listener:

```toml
[webhook]
```

To override hold, timeout, or priority, add a section to `config.toml`:

```toml
[message.schedules.notification]
hold = 60
timeout = 60
priority = 8
```

| Override key | Default | Description |
|---|---|---|
| `hold` | `120` | Seconds to show the message |
| `timeout` | `600` | Seconds the message can wait in the queue before being discarded |
| `priority` | `8` | Display priority (0–10) |

Friend display metadata is stored per-friend by the registration flow:

```toml
[message.friends.alice]
color = "R"
```

These sections are written automatically by the registration action. You can also
edit them manually. Valid color values: `R` (red), `O` (orange), `Y` (yellow),
`G` (green), `B` (blue), `V` (violet), `W` (white), `K` (black), `H` (❤️ heart).

Credential hashes (written alongside the friend config):

```toml
[webhook.credentials.alice]
secret_hash = "$argon2id$..."
webhooks = ["message"]
```

These are managed by the registration flow — do not edit manually.

## Registration

### Register a friend (admin Shortcut)

The admin uses their named credential to register a friend. The Shortcut generates
a random 3-word passphrase client-side, registers it with the board, then shares the
passphrase with the friend.

The admin credential can be any `[webhook.credentials.*]` entry scoped to `message`.
Use the same `python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('your-secret'))"` command to hash it, and put the plaintext in the Shortcut's import question.

A template Shortcut is available at `content/contrib/shortcuts/Register Vestaboard Friend.shortcut`.
Import it and fill in the two import questions. To build manually:

> **Note on variable naming**: in Shortcuts, variables are named at the point of use —
> when you tap a magic variable in a later action, you'll see an option to rename it there.
> There is no separate "Set Variable" step needed.

**Build the admin Shortcut:**

1. Open Shortcuts → tap **+** → name it "Register Vestaboard Friend"
2. Add **Ask for Input** → Type: Text, Prompt: "What's your friend's name?",
   Allow Multiple Lines: off
3. Add **List** → 9 items: `Red`, `Orange`, `Yellow`, `Green`, `Blue`, `Violet`, `White`,
   `Black`, `Heart`
4. Add **Choose from List** → input: the List from step 3, Prompt: "What color do you want to use?"
5. Generate the passphrase:
   - Add **Text** → paste the wordlist (one word per line — see below)
   - Add **Split Text** → input: the Text above, Separator: New Lines
   - Add **Get Item from List** → List: Split Text result, Item: **Random Item**
     (rename output to `word1` when wiring the next step)
   - Repeat twice more → rename outputs `word2`, `word3`
   - Add **Text** → insert `word1` magic variable, type `-`, insert `word2`, type `-`,
     insert `word3` (rename output to `passphrase` when wiring the next step)
6. Add **Text** → paste the webhook secret value here (replaced by import question);
   rename output to `secret` when wiring the next step
7. Add **Get Contents of URL**:
   - URL: `https://<webhook-url>/webhook/message` (replaced by import question)
   - Method: POST
   - Header: `X-Webhook-Secret` → `secret` magic variable from step 6
   - Body type: JSON — add keys (all Text type):
     - `action` = `register`
     - `name` = Ask for Input result from step 2 (rename to `name`)
     - `color` = Choose from List result from step 4 (rename to `color`)
     - `passphrase` = Text result from step 5 (rename to `passphrase`)
   - No `Content-Type` header needed — Shortcuts sets it automatically for JSON bodies
8. Add **Text** → `Message my Vestaboard! Your passphrase: ` + `passphrase` magic variable
   (rename output to `sharing`)
9. Add **Share** → input: `sharing`
10. Tap the **ⓘ** button → **Import Questions** → add two questions:
    - Question: `Webhook URL`, Parameter: URL field of Get Contents of URL, Default: blank
    - Question: `Webhook secret`, Parameter: the Text action from step 6, Default: blank

Share via iCloud link → send to yourself only. This Shortcut holds your admin credential secret.

### Friend Shortcut

Each friend gets a personalized copy of this Shortcut with your webhook URL pre-filled.
They only need to enter their passphrase on import.

A template Shortcut is available at `content/contrib/shortcuts/Message Vestaboard.shortcut`.
See **Personalizing for a friend** below. To build manually:

**Build the friend Shortcut:**

1. Open Shortcuts → tap **+** → name it "Message Vestaboard"
2. Add **Ask for Input** → Type: Text, Prompt: "What do you want to say?",
   Allow Multiple Lines: off
3. Add **Text** → paste the passphrase here (replaced by import question);
   rename output to `raw` when wiring the next step
4. Add **Split Text** → Input: `raw`, Separator: Custom, value `": "`
5. Add **Get Item from List** → Input: Split Text result, Item: **Last Item**;
   rename output to `passphrase` when wiring the next step
   *(This lets your friend paste either just their passphrase or the full share message
   — e.g. "Message my Vestaboard! Your passphrase: word-word-word" — and it works either way.)*
6. Add **Get Contents of URL**:
   - URL: `https://<webhook-url>/webhook/message` (replaced by import question)
   - Method: POST
   - Header: `X-Webhook-Secret` → `passphrase` magic variable from step 5
   - Body type: JSON — add one key (Text type):
     - `message` = Ask for Input result from step 2 (rename to `message`)
   - No `Content-Type` header needed — Shortcuts sets it automatically for JSON bodies
7. Add **List** → 5 items:
   `It's on the way! 🪄`, `Sent to the board! ✨`, `Your message is flipping! 🎰`,
   `Message launched! 🚀`, `The flaps are flying! 🪁`
8. Add **Get Item from List** → Input: List from step 7, Item: **Random Item**;
   rename output to `confirmation` when wiring the next step
9. Add **Show Alert** → Message: `confirmation` magic variable from step 8;
   untick Show Cancel Button; leave title blank
10. Tap the **ⓘ** button → **Import Questions** → add two questions:
    - Question: `Webhook URL`, Parameter: URL field of Get Contents of URL, Default: blank
    - Question: `Passphrase`, Parameter: the Text action from step 3, Default: blank

### Personalizing for a friend

The template has both `Webhook URL` and `Passphrase` as import questions. To share a
pre-configured copy where your friend only fills in their passphrase:

1. Import the template Shortcut and fill in your `Webhook URL` on import
2. Open the Shortcut → tap **ⓘ** → **Import Questions** → delete the `Webhook URL` question
   (this ensures your URL is embedded and not exposed or overwritten when your friend imports it)
3. Rename to "Message [Your Name]'s Vestaboard" so it's clear whose board it reaches
4. Share via iCloud link → your friend imports it and fills in only their `Passphrase`

The passphrase is their identity — keep each friend's iCloud link private.

## Payload schema

### Message post

```json
{ "message": "Hey, the food is ready!" }
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `message` | string | Yes | — | Body text. Newlines produce multiple display rows. Color tag syntax (`[R]`, `[G]`, etc.) is not interpreted — brackets are stripped and the letter is kept. |
| `action` | string | No | `"message"` | Must be `"message"` or omitted for posting. |

The sender is identified by their `X-Webhook-Secret` passphrase. No other fields
are needed — name and color come from the board's config.

### Registration

```json
{
  "action": "register",
  "name": "alice",
  "color": "Red",
  "passphrase": "river-candle-bench"
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `action` | string | Yes | — | Must be `"register"` |
| `name` | string | Yes | — | Friend's name; spaces become hyphens, then lowercased (e.g. "Bob Smith" → `bob-smith`); shown on the board uppercased |
| `color` | string | No | `"White"` | Header color: full name (Red, Orange, Yellow, Green, Blue, Violet, White, Black, Heart) or single-letter code |
| `passphrase` | string | Yes | — | Plaintext passphrase (min 8 chars); hashed before storing |

Re-registering an existing `name` overwrites the previous credential and display config.

Registration must be sent using any named credential (not a friend passphrase that has
no `[webhook.credentials.*]` entry with `webhooks = ["message"]`).

## Display format

**Note (3×15):**

```
[R] FROM ALICE
HEY THE FOOD
IS READY
```

**Flagship (6×22):**

```
[R] FROM ALICE
HEY THE FOOD IS READY
```

- Row 1: `{color} FROM {NAME}` — name hard-capped at 8 chars on Note, 15 on Flagship
- Rows 2+: message body, word-wrapped to board width with ellipsis truncation; excess rows dropped

## Security notes

- Per-friend passphrases are stored as **argon2id hashes** in `config.toml`. Even if
  `config.toml` is exposed, raw passphrases cannot be recovered without brute force.
- Each credential is scoped to the `message` endpoint only — friend passphrases
  cannot authenticate other webhooks (Plex, Notion, etc.).
- The admin Shortcut holds the main webhook secret. Share it only with yourself.
- Friend passphrases are embedded in their Shortcuts. Anyone with the iCloud link
  can read the passphrase, so share links privately (iMessage, not public URLs).
- For external webhook access, use Cloudflare Tunnel or a TLS-terminating reverse
  proxy. See `docs/webhook-reverse-proxy.md`.

## Passphrase wordlist

Paste this list into the **Text** action in the admin Shortcut (one word per line).
The list contains 500 common English words for generating 3-word passphrases.

```
able
acid
aged
area
army
axle
baby
back
ball
band
bank
barn
base
bath
beam
bear
beat
bell
bind
bird
bite
blow
blue
bolt
bond
bone
book
boot
bowl
brew
brim
buck
bulk
bull
burn
cafe
cage
cake
call
calm
camp
card
care
cart
cash
cast
cave
cell
chip
clan
claw
clay
clip
club
coal
coat
coil
cold
cone
cord
core
corn
cost
cozy
crab
crew
crop
cube
curl
dare
dark
dart
date
dawn
deal
deck
deep
deer
dent
desk
dice
dine
dirt
dish
disk
dive
dock
dome
dose
dove
draw
drop
drum
duck
dune
dusk
dust
earn
edge
epic
face
fact
fade
fail
fair
fall
fame
farm
fast
fate
fear
feat
feed
fern
fill
film
find
fine
fire
firm
fish
flag
flat
flip
flow
foam
fold
folk
fond
font
food
fool
ford
fork
form
fort
free
frog
fuel
full
gain
gale
game
gate
gear
glow
glue
goal
gold
golf
gown
grab
gram
grid
grim
grip
grit
grow
gulf
hail
hale
hall
halt
hand
hard
harm
harp
haze
heal
heap
heat
heel
helm
herd
high
hike
hill
hint
hold
hole
home
hook
hope
horn
hose
host
hulk
hull
hunt
inch
jade
jail
jest
join
jump
jury
keen
keep
kick
kind
king
knob
knot
lack
lake
land
lane
last
late
lead
leaf
lean
lend
lens
lick
lift
lime
limp
line
link
lion
list
load
loft
loom
loop
lore
lost
loud
lull
lure
mace
main
mane
mare
mark
mask
mast
maze
meld
melt
mesh
mild
milk
mill
mind
mint
mire
mist
moat
mock
mode
mold
mole
mood
moon
moss
mule
myth
nail
name
near
neck
nest
next
nice
node
noon
norm
note
oath
open
orca
pace
page
pail
pain
palm
part
past
path
peak
peel
perk
pine
pink
pipe
plan
play
plod
plot
plum
plus
poem
poll
polo
pond
pose
pour
prep
prey
prod
prop
pull
pump
push
rail
rain
ramp
rank
rare
rash
raze
reap
reel
rein
rend
rest
rice
rich
ride
rift
rise
risk
roam
roar
rock
roll
roof
rook
room
rope
rose
rove
ruin
rule
rush
rust
safe
sage
sail
sake
sale
salt
sand
sash
save
scan
seal
seam
seed
seek
self
shed
ship
shoe
shop
show
silk
sing
sink
sire
skip
slam
slap
sled
slim
slip
slow
snap
snow
soak
soar
soft
sole
song
soot
sort
soul
soup
span
spin
spit
spot
star
stay
stem
step
stop
stow
stub
stun
suck
suit
sulk
surf
swap
sway
swim
tack
tail
tale
tall
tame
tarp
task
tell
tent
term
text
thin
tick
tide
tile
time
tire
toad
toll
tomb
tome
toil
torn
toss
tour
town
trap
tree
trek
trim
trio
trip
true
tuft
tune
turf
turn
twig
twin
undo
unit
vent
view
vine
void
vote
wade
wage
wake
walk
wall
ward
warm
warp
wart
wave
weld
well
west
whim
wild
will
wind
wing
wink
wish
woke
worm
wrap
yard
yell
zone
zoom
acorn
amber
angel
anvil
apple
apron
attic
azure
badger
baker
barge
bench
berry
birch
blade
blaze
bloom
blown
bluff
board
boost
brace
braid
brand
brave
bread
brick
brine
brisk
brook
broth
brown
brush
budge
bulge
bunch
burst
cabin
camel
candy
cedar
chain
chalk
charm
chase
check
chess
chimp
cider
cinch
civil
clamp
clang
clash
clasp
cleat
cleft
cloak
clone
cloud
clump
coach
coast
cobra
comet
coral
couch
cough
count
court
cover
craft
crane
creak
creep
crest
creek
crisp
crown
crumb
crush
curve
daisy
dance
denim
depot
derby
dingo
disco
dodge
dowel
draft
drain
drape
drawl
dream
droop
drove
drown
dwarf
eagle
easel
eight
elder
ember
epoch
erode
evoke
exile
ezine
fable
facet
faith
fancy
feast
fence
ferry
fever
finch
fjord
flake
flame
flank
flare
flask
fleck
flesh
flick
flint
flock
flood
floor
flora
floss
flute
focus
force
forge
found
franc
frond
frost
froth
froze
fruit
fungi
grail
grand
grant
grape
grasp
grate
gravel
graze
greed
green
greet
grind
groan
grope
gross
grove
guard
guava
guild
guise
gulch
gusto
hazel
heron
herbs
hoist
holly
honor
hover
husky
icicle
image
infer
input
irony
ivory
jelly
jewel
joust
juice
kayak
kebab
kneel
lance
larch
laser
latch
later
lemon
level
light
lilac
llama
lodge
lofty
lumen
lunar
lyric
magic
maple
march
merit
micro
might
mirth
mixed
moose
morse
mount
mourn
mulch
music
naive
noble
noise
notch
nudge
nurse
nymph
oak
oboe
olive
onyx
optic
orbit
order
organ
outer
oxbow
oxide
ozone
panther
paper
paste
pause
peach
pearl
pedal
penny
perch
petal
phase
pilot
pinch
pixel
pizza
place
plank
plant
plasm
plaza
pluck
plumb
plume
plunk
poise
poker
poppy
power
press
price
pride
prime
print
prize
probe
prune
psalm
pulse
pylon
quail
quake
quark
quest
queue
quota
quote
racer
radar
radish
radar
rally
ranch
range
rapid
raven
reach
realm
rebel
recap
resin
ridge
right
rivet
robin
rodeo
rouge
rough
round
royal
ruby
rudder
ruler
rupee
rustle
sable
salsa
sandy
sauce
savor
scale
scope
score
scout
screw
scrub
serif
shake
shall
shale
shard
shark
sheen
shelf
shell
shift
shine
shore
shout
shrug
siege
sigma
silky
skate
skill
skimp
skull
slate
sleek
sleet
slick
sloth
slump
smash
smear
smell
smock
snack
snail
snare
sneak
sniff
snore
snout
solar
solid
south
space
spade
spare
spark
spawn
spear
speck
speed
spell
spend
spice
spike
spine
spool
spore
spray
sprig
spunk
squad
squat
squid
stain
stale
stalk
stall
stamp
stark
stash
stave
steak
steam
steel
steep
steer
stern
stiff
still
sting
stink
stir
stock
stomp
stone
stork
stout
straw
stray
strip
strum
strut
stuck
study
stump
style
sugar
sully
sunup
surge
swamp
swear
sweat
sweep
sweet
swift
swirl
sword
talon
tapir
taunt
taupe
thorn
throb
thumb
thyme
tiara
tiger
tight
timer
titan
toast
token
torch
total
touch
tough
towel
toxic
trace
track
trade
trail
train
trait
tramp
trash
trawl
triad
tribe
trick
trout
truce
truck
tulip
tuple
tutor
twine
typed
ulcer
ultra
umbra
uncle
upper
urban
usage
utter
valor
vapid
vault
venom
verge
vigor
viola
viral
vista
vixen
vocal
vodka
vortex
waltz
wasp
watch
water
weave
wedge
wheat
where
whirl
winch
witch
woven
wreck
wring
yacht
yearn
yield
youth
zebra
zesty
zilch
```

## Keeping data current

This integration has no hardcoded external data. The wordlist above is static and
does not require updates. Argon2id parameters default to `argon2-cffi`'s built-in
defaults (time=3, memory=65536, parallelism=4); see `argon2-cffi` release notes
for any recommended parameter changes.
