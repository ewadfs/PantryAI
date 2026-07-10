"""Seed ingredient_master with ~400 common US grocery ingredients.

Idempotent: upserts on canonical_name (a unique column). Safe to re-run.

Each row: (canonical_name, display_name, category, typical_unit,
           shelf_life_days, is_pantry_staple, [common_aliases])

Run from the backend/ directory:
    .venv/Scripts/python.exe scripts/seed_ingredients.py
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.database import AsyncSessionLocal
from app.models.ingredient import IngredientMaster

T = True
F = False

# ---------------------------------------------------------------------------
# PRODUCE
# ---------------------------------------------------------------------------
PRODUCE = [
    ("banana", "Banana", "each", 7, F, ["bananas", "ripe banana", "yellow banana", "cavendish banana"]),
    ("gala_apple", "Gala Apple", "each", 21, F, ["gala apples", "apple", "red apple", "fresh apple"]),
    ("honeycrisp_apple", "Honeycrisp Apple", "each", 21, F, ["honeycrisp", "honeycrisp apples", "apple", "crisp apple"]),
    ("granny_smith_apple", "Granny Smith Apple", "each", 21, F, ["granny smith", "green apple", "tart apple", "sour apple"]),
    ("navel_orange", "Navel Orange", "each", 21, F, ["navel oranges", "orange", "seedless orange", "fresh orange"]),
    ("lemon", "Lemon", "each", 21, F, ["lemons", "fresh lemon", "yellow lemon", "citrus lemon"]),
    ("lime", "Lime", "each", 21, F, ["limes", "fresh lime", "green lime", "key lime"]),
    ("red_grapes", "Red Grapes", "bag", 7, F, ["red grapes", "seedless red grapes", "grapes", "table grapes"]),
    ("green_grapes", "Green Grapes", "bag", 7, F, ["green grapes", "seedless green grapes", "grapes", "white grapes"]),
    ("strawberry", "Strawberries", "container", 5, F, ["strawberries", "fresh strawberries", "berries", "strawberry pint"]),
    ("blueberry", "Blueberries", "container", 7, F, ["blueberries", "fresh blueberries", "berries", "blueberry pint"]),
    ("raspberry", "Raspberries", "container", 3, F, ["raspberries", "fresh raspberries", "berries", "red raspberries"]),
    ("blackberry", "Blackberries", "container", 4, F, ["blackberries", "fresh blackberries", "berries", "black berries"]),
    ("watermelon", "Watermelon", "each", 7, F, ["watermelons", "seedless watermelon", "melon", "whole watermelon"]),
    ("cantaloupe", "Cantaloupe", "each", 7, F, ["cantaloupes", "muskmelon", "melon", "rock melon"]),
    ("honeydew_melon", "Honeydew Melon", "each", 7, F, ["honeydew", "honeydew melon", "green melon", "melon"]),
    ("pineapple", "Pineapple", "each", 5, F, ["pineapples", "fresh pineapple", "whole pineapple", "golden pineapple"]),
    ("mango", "Mango", "each", 6, F, ["mangoes", "mangos", "fresh mango", "ripe mango"]),
    ("avocado", "Avocado", "each", 5, F, ["avocados", "hass avocado", "fresh avocado", "haas avocado"]),
    ("peach", "Peach", "each", 5, F, ["peaches", "fresh peach", "yellow peach", "ripe peach"]),
    ("nectarine", "Nectarine", "each", 5, F, ["nectarines", "fresh nectarine", "yellow nectarine"]),
    ("plum", "Plum", "each", 7, F, ["plums", "fresh plum", "red plum", "black plum"]),
    ("pear", "Pear", "each", 14, F, ["pears", "bartlett pear", "anjou pear", "fresh pear"]),
    ("kiwi", "Kiwi", "each", 10, F, ["kiwis", "kiwifruit", "kiwi fruit", "fresh kiwi"]),
    ("cherry", "Cherries", "bag", 5, F, ["cherries", "fresh cherries", "sweet cherries", "bing cherries"]),
    ("pomegranate", "Pomegranate", "each", 30, F, ["pomegranates", "fresh pomegranate", "whole pomegranate"]),
    ("grapefruit", "Grapefruit", "each", 21, F, ["grapefruits", "ruby red grapefruit", "pink grapefruit", "citrus"]),
    ("clementine", "Clementines", "bag", 14, F, ["clementines", "cuties", "mandarin oranges", "halos"]),
    ("tomato", "Tomato", "each", 7, F, ["tomatoes", "fresh tomato", "vine tomato", "beefsteak tomato"]),
    ("cherry_tomato", "Cherry Tomatoes", "container", 7, F, ["cherry tomatoes", "grape tomatoes", "small tomatoes", "salad tomatoes"]),
    ("roma_tomato", "Roma Tomato", "each", 7, F, ["roma tomatoes", "plum tomatoes", "italian tomatoes", "sauce tomatoes"]),
    ("cucumber", "Cucumber", "each", 7, F, ["cucumbers", "fresh cucumber", "english cucumber", "slicing cucumber"]),
    ("red_bell_pepper", "Red Bell Pepper", "each", 10, F, ["red bell pepper", "red pepper", "red capsicum", "sweet red pepper"]),
    ("green_bell_pepper", "Green Bell Pepper", "each", 12, F, ["green bell pepper", "green pepper", "green capsicum", "bell pepper"]),
    ("yellow_bell_pepper", "Yellow Bell Pepper", "each", 10, F, ["yellow bell pepper", "yellow pepper", "sweet yellow pepper"]),
    ("jalapeno", "Jalapeño", "each", 14, F, ["jalapenos", "jalapeno pepper", "hot pepper", "green chili"]),
    ("romaine_lettuce", "Romaine Lettuce", "head", 7, F, ["romaine", "romaine hearts", "cos lettuce", "salad lettuce"]),
    ("iceberg_lettuce", "Iceberg Lettuce", "head", 7, F, ["iceberg", "iceberg head", "crisphead lettuce", "salad lettuce"]),
    ("spinach", "Spinach", "bag", 5, F, ["baby spinach", "fresh spinach", "spinach leaves", "leafy greens"]),
    ("kale", "Kale", "bunch", 5, F, ["fresh kale", "curly kale", "lacinato kale", "leafy greens"]),
    ("arugula", "Arugula", "bag", 4, F, ["baby arugula", "rocket", "fresh arugula", "salad greens"]),
    ("broccoli", "Broccoli", "each", 7, F, ["broccoli crowns", "fresh broccoli", "broccoli florets", "broccoli head"]),
    ("cauliflower", "Cauliflower", "each", 7, F, ["fresh cauliflower", "cauliflower head", "white cauliflower", "cauliflower florets"]),
    ("carrot", "Carrots", "bag", 21, F, ["carrots", "baby carrots", "fresh carrots", "whole carrots"]),
    ("celery", "Celery", "each", 14, F, ["celery stalks", "celery bunch", "fresh celery", "celery hearts"]),
    ("yellow_onion", "Yellow Onion", "each", 30, F, ["yellow onions", "onion", "cooking onion", "brown onion"]),
    ("red_onion", "Red Onion", "each", 30, F, ["red onions", "purple onion", "onion", "salad onion"]),
    ("white_onion", "White Onion", "each", 30, F, ["white onions", "onion", "cooking onion"]),
    ("green_onion", "Green Onion", "bunch", 7, F, ["green onions", "scallions", "spring onions", "salad onions"]),
    ("garlic", "Garlic", "each", 60, F, ["garlic bulb", "fresh garlic", "garlic cloves", "garlic head"]),
    ("ginger_root", "Ginger Root", "each", 21, F, ["fresh ginger", "ginger", "gingerroot", "ginger knob"]),
    ("russet_potato", "Russet Potato", "bag", 45, F, ["russet potatoes", "baking potato", "idaho potato", "potatoes"]),
    ("red_potato", "Red Potato", "bag", 30, F, ["red potatoes", "new potatoes", "baby red potatoes", "potatoes"]),
    ("yukon_gold_potato", "Yukon Gold Potato", "bag", 30, F, ["yukon gold", "gold potatoes", "yellow potatoes", "potatoes"]),
    ("sweet_potato", "Sweet Potato", "each", 30, F, ["sweet potatoes", "yams", "orange sweet potato", "potatoes"]),
    ("white_mushroom", "White Mushrooms", "container", 7, F, ["white mushrooms", "button mushrooms", "mushrooms", "fresh mushrooms"]),
    ("portobello_mushroom", "Portobello Mushrooms", "container", 7, F, ["portobello", "portabella mushrooms", "large mushrooms", "mushroom caps"]),
    ("zucchini", "Zucchini", "each", 7, F, ["zucchinis", "green squash", "courgette", "summer squash"]),
    ("yellow_squash", "Yellow Squash", "each", 7, F, ["yellow squash", "summer squash", "crookneck squash"]),
    ("green_beans", "Green Beans", "bag", 7, F, ["fresh green beans", "string beans", "snap beans", "haricot verts"]),
    ("asparagus", "Asparagus", "bunch", 5, F, ["fresh asparagus", "asparagus spears", "green asparagus"]),
    ("brussels_sprouts", "Brussels Sprouts", "bag", 10, F, ["brussel sprouts", "fresh brussels sprouts", "sprouts"]),
    ("cabbage", "Cabbage", "each", 30, F, ["green cabbage", "fresh cabbage", "cabbage head", "head cabbage"]),
    ("corn_on_cob", "Corn on the Cob", "each", 5, F, ["corn", "sweet corn", "corn on the cob", "ears of corn"]),
    ("eggplant", "Eggplant", "each", 7, F, ["eggplants", "aubergine", "fresh eggplant", "italian eggplant"]),
    ("butternut_squash", "Butternut Squash", "each", 30, F, ["butternut squash", "winter squash", "squash"]),
    ("cilantro", "Cilantro", "bunch", 7, F, ["fresh cilantro", "coriander leaves", "chinese parsley", "herbs"]),
    ("parsley", "Parsley", "bunch", 7, F, ["fresh parsley", "flat leaf parsley", "italian parsley", "herbs"]),
    ("basil", "Basil", "bunch", 5, F, ["fresh basil", "sweet basil", "basil leaves", "herbs"]),
]

# ---------------------------------------------------------------------------
# MEAT
# ---------------------------------------------------------------------------
MEAT = [
    ("chicken_breast", "Chicken Breast", "lb", 2, F, ["chicken breasts", "boneless skinless chicken breast", "bnls chicken breast", "white meat chicken"]),
    ("chicken_thigh", "Chicken Thigh", "lb", 2, F, ["chicken thighs", "bone-in thighs", "thigh meat", "dark meat chicken"]),
    ("chicken_drumstick", "Chicken Drumsticks", "lb", 2, F, ["chicken drumsticks", "drumsticks", "chicken legs", "dark meat"]),
    ("chicken_wing", "Chicken Wings", "lb", 2, F, ["chicken wings", "wings", "party wings", "buffalo wings"]),
    ("whole_chicken", "Whole Chicken", "each", 2, F, ["whole chicken", "roasting chicken", "fryer chicken", "whole fryer"]),
    ("ground_chicken", "Ground Chicken", "lb", 2, F, ["ground chicken", "minced chicken", "chicken mince"]),
    ("chicken_tenders", "Chicken Tenders", "lb", 2, F, ["chicken tenders", "chicken tenderloins", "tenders", "chicken strips"]),
    ("ground_beef", "Ground Beef", "lb", 3, F, ["ground beef", "hamburger meat", "80/20 ground beef", "minced beef", "ground chuck"]),
    ("beef_chuck_roast", "Beef Chuck Roast", "lb", 3, F, ["chuck roast", "beef chuck", "pot roast", "shoulder roast"]),
    ("ribeye_steak", "Ribeye Steak", "lb", 3, F, ["ribeye", "rib eye steak", "ribeye steaks", "beef ribeye"]),
    ("sirloin_steak", "Sirloin Steak", "lb", 3, F, ["sirloin", "top sirloin", "sirloin steaks", "beef sirloin"]),
    ("beef_brisket", "Beef Brisket", "lb", 4, F, ["brisket", "beef brisket", "whole brisket", "bbq brisket"]),
    ("beef_short_ribs", "Beef Short Ribs", "lb", 3, F, ["short ribs", "beef short ribs", "bone-in short ribs"]),
    ("flank_steak", "Flank Steak", "lb", 3, F, ["flank steak", "beef flank", "london broil"]),
    ("ground_turkey", "Ground Turkey", "lb", 2, F, ["ground turkey", "turkey mince", "lean ground turkey", "93/7 ground turkey"]),
    ("turkey_breast", "Turkey Breast", "lb", 3, F, ["turkey breast", "boneless turkey breast", "roast turkey breast"]),
    ("pork_chop", "Pork Chop", "lb", 3, F, ["pork chops", "bone-in pork chop", "center cut pork chop", "boneless pork chop"]),
    ("pork_tenderloin", "Pork Tenderloin", "lb", 3, F, ["pork tenderloin", "pork loin", "tenderloin", "pork fillet"]),
    ("pork_shoulder", "Pork Shoulder", "lb", 4, F, ["pork shoulder", "pork butt", "boston butt", "picnic roast"]),
    ("ground_pork", "Ground Pork", "lb", 3, F, ["ground pork", "minced pork", "pork mince"]),
    ("pork_ribs", "Pork Ribs", "lb", 3, F, ["pork ribs", "baby back ribs", "spare ribs", "st louis ribs"]),
    ("bacon", "Bacon", "package", 7, F, ["bacon", "sliced bacon", "thick cut bacon", "smoked bacon"]),
    ("ham", "Ham", "lb", 7, F, ["ham", "sliced ham", "spiral ham", "smoked ham"]),
    ("italian_sausage", "Italian Sausage", "lb", 3, F, ["italian sausage", "sweet italian sausage", "hot italian sausage", "sausage links"]),
    ("breakfast_sausage", "Breakfast Sausage", "lb", 3, F, ["breakfast sausage", "sausage patties", "pork sausage", "breakfast links"]),
    ("hot_dog", "Hot Dogs", "package", 14, F, ["hot dogs", "frankfurters", "franks", "wieners"]),
    ("lamb_chop", "Lamb Chops", "lb", 3, F, ["lamb chops", "lamb loin chops", "rack of lamb", "lamb"]),
    ("ground_lamb", "Ground Lamb", "lb", 2, F, ["ground lamb", "minced lamb", "lamb mince"]),
    ("deli_turkey", "Deli Turkey", "lb", 5, F, ["deli turkey", "sliced turkey", "turkey lunch meat", "oven roasted turkey"]),
    ("deli_ham", "Deli Ham", "lb", 5, F, ["deli ham", "sliced ham", "ham lunch meat", "black forest ham"]),
    ("salami", "Salami", "package", 21, F, ["salami", "genoa salami", "hard salami", "sliced salami"]),
    ("pepperoni", "Pepperoni", "package", 30, F, ["pepperoni", "sliced pepperoni", "pizza pepperoni"]),
]

# ---------------------------------------------------------------------------
# SEAFOOD
# ---------------------------------------------------------------------------
SEAFOOD = [
    ("salmon_fillet", "Salmon Fillet", "lb", 2, F, ["salmon", "salmon fillets", "atlantic salmon", "fresh salmon"]),
    ("tilapia", "Tilapia", "lb", 2, F, ["tilapia", "tilapia fillets", "fresh tilapia", "white fish"]),
    ("shrimp", "Shrimp", "lb", 2, F, ["shrimp", "raw shrimp", "jumbo shrimp", "peeled shrimp", "prawns"]),
    ("cod", "Cod", "lb", 2, F, ["cod", "cod fillets", "atlantic cod", "white fish"]),
    ("tuna_steak", "Tuna Steak", "lb", 2, F, ["tuna steak", "ahi tuna", "yellowfin tuna", "fresh tuna"]),
    ("catfish", "Catfish", "lb", 2, F, ["catfish", "catfish fillets", "fresh catfish"]),
    ("crab_legs", "Crab Legs", "lb", 2, F, ["crab legs", "snow crab legs", "king crab legs", "crab"]),
    ("lobster_tail", "Lobster Tail", "each", 2, F, ["lobster tail", "lobster tails", "lobster"]),
    ("scallops", "Scallops", "lb", 2, F, ["scallops", "sea scallops", "bay scallops", "fresh scallops"]),
    ("mussels", "Mussels", "lb", 2, F, ["mussels", "fresh mussels", "black mussels"]),
    ("clams", "Clams", "lb", 2, F, ["clams", "littleneck clams", "fresh clams", "steamer clams"]),
    ("flounder", "Flounder", "lb", 2, F, ["flounder", "flounder fillets", "fluke", "white fish"]),
    ("halibut", "Halibut", "lb", 2, F, ["halibut", "halibut fillets", "halibut steak"]),
    ("mahi_mahi", "Mahi Mahi", "lb", 2, F, ["mahi mahi", "mahi", "dolphinfish", "dorado"]),
    ("oysters", "Oysters", "dozen", 2, F, ["oysters", "fresh oysters", "shucked oysters", "raw oysters"]),
    ("calamari", "Calamari", "lb", 2, F, ["calamari", "squid", "squid rings", "fresh calamari"]),
    ("smoked_salmon", "Smoked Salmon", "package", 14, F, ["smoked salmon", "lox", "nova salmon", "cured salmon"]),
    ("sea_bass", "Sea Bass", "lb", 2, F, ["sea bass", "chilean sea bass", "branzino", "sea bass fillet"]),
    ("trout", "Trout", "lb", 2, F, ["trout", "rainbow trout", "trout fillets", "fresh trout"]),
    ("swordfish", "Swordfish", "lb", 2, F, ["swordfish", "swordfish steak", "fresh swordfish"]),
]

# ---------------------------------------------------------------------------
# DAIRY
# ---------------------------------------------------------------------------
DAIRY = [
    ("whole_milk", "Whole Milk", "gallon", 10, F, ["whole milk", "vitamin d milk", "full fat milk", "milk"]),
    ("milk_2_percent", "2% Milk", "gallon", 10, F, ["2% milk", "reduced fat milk", "two percent milk", "milk"]),
    ("skim_milk", "Skim Milk", "gallon", 10, F, ["skim milk", "fat free milk", "nonfat milk", "milk"]),
    ("heavy_cream", "Heavy Cream", "pint", 14, F, ["heavy cream", "heavy whipping cream", "whipping cream", "double cream"]),
    ("half_and_half", "Half & Half", "quart", 14, F, ["half and half", "half & half", "coffee cream", "light cream"]),
    ("butter", "Butter", "lb", 60, F, ["butter", "salted butter", "sticks of butter", "dairy butter"]),
    ("unsalted_butter", "Unsalted Butter", "lb", 60, T, ["unsalted butter", "sweet cream butter", "baking butter", "butter"]),
    ("cream_cheese", "Cream Cheese", "package", 30, F, ["cream cheese", "philadelphia", "brick cream cheese", "plain cream cheese"]),
    ("sour_cream", "Sour Cream", "container", 21, F, ["sour cream", "light sour cream", "full fat sour cream"]),
    ("cottage_cheese", "Cottage Cheese", "container", 14, F, ["cottage cheese", "small curd cottage cheese", "low fat cottage cheese"]),
    ("greek_yogurt", "Greek Yogurt", "container", 21, F, ["greek yogurt", "plain greek yogurt", "nonfat greek yogurt", "strained yogurt"]),
    ("plain_yogurt", "Plain Yogurt", "container", 21, F, ["plain yogurt", "natural yogurt", "regular yogurt", "yogurt"]),
    ("cheddar_cheese", "Cheddar Cheese", "block", 30, F, ["cheddar", "sharp cheddar", "shredded cheddar", "cheddar cheese"]),
    ("mozzarella_cheese", "Mozzarella Cheese", "bag", 30, F, ["mozzarella", "shredded mozzarella", "fresh mozzarella", "mozz"]),
    ("parmesan_cheese", "Parmesan Cheese", "container", 120, T, ["parmesan", "grated parmesan", "parmigiano reggiano", "parm"]),
    ("swiss_cheese", "Swiss Cheese", "package", 30, F, ["swiss cheese", "sliced swiss", "baby swiss", "swiss"]),
    ("provolone_cheese", "Provolone Cheese", "package", 30, F, ["provolone", "sliced provolone", "provolone cheese"]),
    ("american_cheese", "American Cheese", "package", 45, F, ["american cheese", "american slices", "cheese slices", "singles"]),
    ("feta_cheese", "Feta Cheese", "container", 30, F, ["feta", "crumbled feta", "feta cheese", "greek cheese"]),
    ("ricotta_cheese", "Ricotta Cheese", "container", 14, F, ["ricotta", "whole milk ricotta", "part skim ricotta", "ricotta cheese"]),
    ("string_cheese", "String Cheese", "package", 45, F, ["string cheese", "mozzarella sticks", "cheese sticks", "snack cheese"]),
    ("buttermilk", "Buttermilk", "quart", 14, F, ["buttermilk", "cultured buttermilk", "low fat buttermilk"]),
    ("almond_milk", "Almond Milk", "carton", 45, F, ["almond milk", "unsweetened almond milk", "vanilla almond milk", "nut milk"]),
    ("oat_milk", "Oat Milk", "carton", 45, F, ["oat milk", "oatmilk", "barista oat milk", "plant milk"]),
    ("soy_milk", "Soy Milk", "carton", 45, F, ["soy milk", "soymilk", "unsweetened soy milk", "plant milk"]),
    ("margarine", "Margarine", "container", 90, F, ["margarine", "spread", "buttery spread", "vegetable spread"]),
    ("ghee", "Ghee", "jar", 365, T, ["ghee", "clarified butter", "drawn butter"]),
    ("whipped_cream", "Whipped Cream", "can", 30, F, ["whipped cream", "cool whip", "reddi wip", "aerosol cream"]),
]

# ---------------------------------------------------------------------------
# EGGS
# ---------------------------------------------------------------------------
EGGS = [
    ("large_eggs", "Large Eggs", "dozen", 28, F, ["eggs", "large eggs", "dozen eggs", "grade a eggs"]),
    ("extra_large_eggs", "Extra Large Eggs", "dozen", 28, F, ["extra large eggs", "xl eggs", "jumbo eggs"]),
    ("brown_eggs", "Brown Eggs", "dozen", 28, F, ["brown eggs", "cage free eggs", "organic eggs", "free range eggs"]),
    ("egg_whites", "Egg Whites", "carton", 21, F, ["egg whites", "liquid egg whites", "carton egg whites"]),
    ("liquid_eggs", "Liquid Eggs", "carton", 21, F, ["liquid eggs", "egg beaters", "egg substitute", "whole liquid eggs"]),
]

# ---------------------------------------------------------------------------
# GRAIN (dry pantry goods incl. baking staples)
# ---------------------------------------------------------------------------
GRAIN = [
    ("white_rice", "White Rice", "bag", 730, T, ["white rice", "long grain rice", "long grain white rice", "rice"]),
    ("brown_rice", "Brown Rice", "bag", 365, T, ["brown rice", "whole grain rice", "long grain brown rice", "rice"]),
    ("jasmine_rice", "Jasmine Rice", "bag", 730, T, ["jasmine rice", "thai jasmine rice", "fragrant rice", "rice"]),
    ("basmati_rice", "Basmati Rice", "bag", 730, T, ["basmati rice", "indian basmati", "long grain basmati", "rice"]),
    ("spaghetti", "Spaghetti", "box", 730, T, ["spaghetti", "spaghetti pasta", "thin spaghetti", "pasta"]),
    ("penne", "Penne", "box", 730, T, ["penne", "penne pasta", "penne rigate", "pasta"]),
    ("elbow_macaroni", "Elbow Macaroni", "box", 730, T, ["elbow macaroni", "macaroni", "elbows", "pasta"]),
    ("lasagna_noodles", "Lasagna Noodles", "box", 730, T, ["lasagna noodles", "lasagna sheets", "lasagne", "pasta"]),
    ("egg_noodles", "Egg Noodles", "bag", 545, T, ["egg noodles", "wide egg noodles", "kluski noodles", "pasta"]),
    ("fettuccine", "Fettuccine", "box", 730, T, ["fettuccine", "fettuccini", "fettuccine pasta", "pasta"]),
    ("rotini", "Rotini", "box", 730, T, ["rotini", "spiral pasta", "fusilli", "pasta"]),
    ("angel_hair", "Angel Hair Pasta", "box", 730, T, ["angel hair", "capellini", "thin pasta", "pasta"]),
    ("orzo", "Orzo", "box", 730, T, ["orzo", "orzo pasta", "risoni", "pasta"]),
    ("all_purpose_flour", "All-Purpose Flour", "bag", 365, T, ["all purpose flour", "flour", "ap flour", "plain flour"]),
    ("bread_flour", "Bread Flour", "bag", 365, T, ["bread flour", "high gluten flour", "strong flour"]),
    ("whole_wheat_flour", "Whole Wheat Flour", "bag", 180, T, ["whole wheat flour", "wheat flour", "whole grain flour"]),
    ("rolled_oats", "Rolled Oats", "container", 365, T, ["rolled oats", "old fashioned oats", "oatmeal", "oats"]),
    ("steel_cut_oats", "Steel Cut Oats", "container", 365, T, ["steel cut oats", "irish oats", "scottish oats", "oats"]),
    ("quinoa", "Quinoa", "bag", 730, T, ["quinoa", "white quinoa", "tri color quinoa", "grain"]),
    ("couscous", "Couscous", "box", 730, T, ["couscous", "moroccan couscous", "pearl couscous", "grain"]),
    ("barley", "Pearl Barley", "bag", 365, T, ["barley", "pearl barley", "pearled barley", "grain"]),
    ("farro", "Farro", "bag", 365, T, ["farro", "emmer wheat", "whole farro", "grain"]),
    ("cornmeal", "Cornmeal", "bag", 365, T, ["cornmeal", "yellow cornmeal", "polenta", "corn meal"]),
    ("breadcrumbs", "Breadcrumbs", "container", 365, T, ["breadcrumbs", "bread crumbs", "seasoned breadcrumbs", "plain breadcrumbs"]),
    ("panko", "Panko", "container", 365, T, ["panko", "panko breadcrumbs", "japanese breadcrumbs"]),
    ("granulated_sugar", "Granulated Sugar", "bag", 730, T, ["sugar", "granulated sugar", "white sugar", "cane sugar"]),
    ("brown_sugar", "Brown Sugar", "bag", 545, T, ["brown sugar", "light brown sugar", "dark brown sugar", "packed brown sugar"]),
    ("powdered_sugar", "Powdered Sugar", "bag", 545, T, ["powdered sugar", "confectioners sugar", "icing sugar", "10x sugar"]),
    ("baking_powder", "Baking Powder", "can", 365, T, ["baking powder", "double acting baking powder", "leavening", "baking leavener"]),
    ("baking_soda", "Baking Soda", "box", 730, T, ["baking soda", "sodium bicarbonate", "bicarbonate of soda"]),
    ("cornstarch", "Cornstarch", "box", 730, T, ["cornstarch", "corn starch", "cornflour", "thickener"]),
    ("active_dry_yeast", "Active Dry Yeast", "packet", 365, T, ["active dry yeast", "yeast", "instant yeast", "bread yeast"]),
    ("cocoa_powder", "Cocoa Powder", "container", 545, T, ["cocoa powder", "unsweetened cocoa", "baking cocoa", "dutch cocoa"]),
    ("vanilla_extract", "Vanilla Extract", "bottle", 1095, T, ["vanilla extract", "pure vanilla", "vanilla", "vanilla essence"]),
    ("chocolate_chips", "Chocolate Chips", "bag", 365, T, ["chocolate chips", "semi sweet chips", "chocolate morsels", "baking chips"]),
    ("dry_black_beans", "Dried Black Beans", "bag", 730, T, ["dried black beans", "black beans", "turtle beans", "dry beans"]),
    ("dry_lentils", "Dried Lentils", "bag", 730, T, ["lentils", "dried lentils", "green lentils", "red lentils"]),
    ("cereal_cornflakes", "Corn Flakes Cereal", "box", 270, F, ["corn flakes", "cornflakes", "breakfast cereal", "cereal"]),
    ("granola", "Granola", "bag", 180, F, ["granola", "granola cereal", "oat granola", "breakfast granola"]),
]

# ---------------------------------------------------------------------------
# CANNED
# ---------------------------------------------------------------------------
CANNED = [
    ("canned_tuna", "Canned Tuna", "can", 1095, T, ["canned tuna", "tuna", "chunk light tuna", "albacore tuna", "tuna in water"]),
    ("canned_black_beans", "Canned Black Beans", "can", 730, T, ["canned black beans", "black beans", "black turtle beans"]),
    ("canned_kidney_beans", "Canned Kidney Beans", "can", 730, T, ["canned kidney beans", "kidney beans", "red kidney beans", "dark red kidney beans"]),
    ("canned_chickpeas", "Canned Chickpeas", "can", 730, T, ["canned chickpeas", "chickpeas", "garbanzo beans", "ceci beans"]),
    ("canned_pinto_beans", "Canned Pinto Beans", "can", 730, T, ["canned pinto beans", "pinto beans", "beans"]),
    ("canned_cannellini_beans", "Canned Cannellini Beans", "can", 730, T, ["canned cannellini beans", "cannellini beans", "white kidney beans", "white beans"]),
    ("canned_corn", "Canned Corn", "can", 730, T, ["canned corn", "sweet corn", "whole kernel corn", "corn"]),
    ("canned_green_beans", "Canned Green Beans", "can", 730, T, ["canned green beans", "cut green beans", "string beans"]),
    ("canned_peas", "Canned Peas", "can", 730, T, ["canned peas", "sweet peas", "green peas", "peas"]),
    ("diced_tomatoes", "Diced Tomatoes", "can", 730, T, ["diced tomatoes", "canned diced tomatoes", "petite diced tomatoes", "tomatoes"]),
    ("crushed_tomatoes", "Crushed Tomatoes", "can", 730, T, ["crushed tomatoes", "canned crushed tomatoes", "tomato puree"]),
    ("tomato_paste", "Tomato Paste", "can", 730, T, ["tomato paste", "double concentrate", "tomato concentrate"]),
    ("tomato_sauce", "Tomato Sauce", "can", 730, T, ["tomato sauce", "canned tomato sauce", "plain tomato sauce"]),
    ("chicken_broth", "Chicken Broth", "carton", 545, T, ["chicken broth", "chicken stock", "chicken bouillon", "broth"]),
    ("beef_broth", "Beef Broth", "carton", 545, T, ["beef broth", "beef stock", "beef bouillon", "broth"]),
    ("vegetable_broth", "Vegetable Broth", "carton", 545, T, ["vegetable broth", "veggie broth", "vegetable stock", "broth"]),
    ("coconut_milk", "Coconut Milk", "can", 730, T, ["coconut milk", "canned coconut milk", "full fat coconut milk"]),
    ("canned_pumpkin", "Canned Pumpkin", "can", 730, T, ["canned pumpkin", "pumpkin puree", "pure pumpkin", "pumpkin"]),
    ("canned_peaches", "Canned Peaches", "can", 730, F, ["canned peaches", "sliced peaches", "peaches in syrup"]),
    ("canned_pineapple", "Canned Pineapple", "can", 730, F, ["canned pineapple", "pineapple chunks", "crushed pineapple", "pineapple tidbits"]),
    ("mandarin_oranges", "Mandarin Oranges", "can", 730, F, ["mandarin oranges", "canned mandarins", "mandarin segments"]),
    ("canned_salmon", "Canned Salmon", "can", 1095, T, ["canned salmon", "pink salmon", "salmon"]),
    ("canned_sardines", "Sardines", "can", 1095, T, ["sardines", "canned sardines", "sardines in oil"]),
    ("canned_olives", "Canned Olives", "can", 545, T, ["canned olives", "black olives", "sliced olives", "olives"]),
    ("canned_mushrooms", "Canned Mushrooms", "can", 730, T, ["canned mushrooms", "sliced mushrooms", "mushrooms"]),
    ("refried_beans", "Refried Beans", "can", 545, T, ["refried beans", "canned refried beans", "frijoles refritos"]),
    ("canned_chili", "Canned Chili", "can", 730, F, ["canned chili", "chili con carne", "chili with beans", "chili"]),
    ("chicken_noodle_soup", "Chicken Noodle Soup", "can", 730, F, ["chicken noodle soup", "canned soup", "chicken soup", "soup"]),
    ("cream_of_mushroom_soup", "Cream of Mushroom Soup", "can", 730, T, ["cream of mushroom soup", "canned cream soup", "mushroom soup"]),
    ("evaporated_milk", "Evaporated Milk", "can", 545, T, ["evaporated milk", "canned milk", "unsweetened condensed milk"]),
    ("condensed_milk", "Sweetened Condensed Milk", "can", 545, T, ["sweetened condensed milk", "condensed milk", "canned condensed milk"]),
    ("artichoke_hearts", "Artichoke Hearts", "can", 545, T, ["artichoke hearts", "canned artichokes", "quartered artichokes"]),
]

# ---------------------------------------------------------------------------
# CONDIMENT (incl. spreads)
# ---------------------------------------------------------------------------
CONDIMENT = [
    ("ketchup", "Ketchup", "bottle", 365, T, ["ketchup", "catsup", "tomato ketchup", "heinz ketchup"]),
    ("yellow_mustard", "Yellow Mustard", "bottle", 365, T, ["yellow mustard", "mustard", "prepared mustard", "classic mustard"]),
    ("dijon_mustard", "Dijon Mustard", "jar", 365, T, ["dijon mustard", "dijon", "grey poupon", "french mustard"]),
    ("mayonnaise", "Mayonnaise", "jar", 180, T, ["mayonnaise", "mayo", "real mayo", "hellmanns"]),
    ("sweet_relish", "Sweet Relish", "jar", 365, T, ["sweet relish", "pickle relish", "relish", "dill relish"]),
    ("bbq_sauce", "BBQ Sauce", "bottle", 365, T, ["bbq sauce", "barbecue sauce", "barbeque sauce", "sweet baby rays"]),
    ("ranch_dressing", "Ranch Dressing", "bottle", 120, F, ["ranch dressing", "ranch", "buttermilk ranch", "hidden valley ranch"]),
    ("italian_dressing", "Italian Dressing", "bottle", 180, F, ["italian dressing", "zesty italian", "italian vinaigrette"]),
    ("caesar_dressing", "Caesar Dressing", "bottle", 120, F, ["caesar dressing", "caesar", "creamy caesar dressing"]),
    ("honey_mustard", "Honey Mustard", "bottle", 240, T, ["honey mustard", "honey dijon", "honey mustard dressing"]),
    ("tartar_sauce", "Tartar Sauce", "jar", 180, F, ["tartar sauce", "tartare sauce", "seafood sauce"]),
    ("cocktail_sauce", "Cocktail Sauce", "jar", 240, F, ["cocktail sauce", "shrimp cocktail sauce", "seafood cocktail sauce"]),
    ("horseradish", "Horseradish", "jar", 180, F, ["horseradish", "prepared horseradish", "horseradish sauce"]),
    ("salsa", "Salsa", "jar", 60, F, ["salsa", "chunky salsa", "tomato salsa", "picante sauce"]),
    ("guacamole", "Guacamole", "container", 7, F, ["guacamole", "guac", "avocado dip", "prepared guacamole"]),
    ("hummus", "Hummus", "container", 10, F, ["hummus", "roasted red pepper hummus", "classic hummus", "chickpea dip"]),
    ("dill_pickles", "Dill Pickles", "jar", 365, T, ["dill pickles", "pickles", "kosher dill pickles", "pickle spears"]),
    ("steak_sauce", "Steak Sauce", "bottle", 365, T, ["steak sauce", "a1 sauce", "a.1.", "brown sauce"]),
    ("thousand_island", "Thousand Island Dressing", "bottle", 120, F, ["thousand island", "1000 island dressing", "russian dressing"]),
    ("blue_cheese_dressing", "Blue Cheese Dressing", "bottle", 90, F, ["blue cheese dressing", "bleu cheese dressing", "chunky blue cheese"]),
    ("peanut_butter", "Peanut Butter", "jar", 365, T, ["peanut butter", "creamy peanut butter", "crunchy peanut butter", "pb"]),
    ("almond_butter", "Almond Butter", "jar", 365, T, ["almond butter", "creamy almond butter", "nut butter"]),
    ("grape_jelly", "Grape Jelly", "jar", 365, T, ["grape jelly", "jelly", "grape jam", "welchs jelly"]),
    ("strawberry_jam", "Strawberry Jam", "jar", 365, T, ["strawberry jam", "strawberry preserves", "strawberry jelly", "jam"]),
    ("honey", "Honey", "bottle", 1095, T, ["honey", "raw honey", "clover honey", "pure honey"]),
    ("maple_syrup", "Maple Syrup", "bottle", 365, T, ["maple syrup", "pure maple syrup", "pancake syrup", "syrup"]),
    ("nutella", "Hazelnut Spread", "jar", 365, T, ["nutella", "hazelnut spread", "chocolate hazelnut spread"]),
    ("cranberry_sauce", "Cranberry Sauce", "can", 545, F, ["cranberry sauce", "jellied cranberry sauce", "whole berry cranberry sauce"]),
    ("apple_butter", "Apple Butter", "jar", 365, T, ["apple butter", "spiced apple butter", "apple spread"]),
]

# ---------------------------------------------------------------------------
# OIL_VINEGAR
# ---------------------------------------------------------------------------
OIL_VINEGAR = [
    ("olive_oil", "Olive Oil", "bottle", 545, T, ["olive oil", "pure olive oil", "light olive oil", "cooking oil"]),
    ("extra_virgin_olive_oil", "Extra Virgin Olive Oil", "bottle", 365, T, ["extra virgin olive oil", "evoo", "olive oil", "cold pressed olive oil"]),
    ("vegetable_oil", "Vegetable Oil", "bottle", 365, T, ["vegetable oil", "cooking oil", "soybean oil", "frying oil"]),
    ("canola_oil", "Canola Oil", "bottle", 365, T, ["canola oil", "rapeseed oil", "cooking oil"]),
    ("coconut_oil", "Coconut Oil", "jar", 730, T, ["coconut oil", "virgin coconut oil", "refined coconut oil"]),
    ("avocado_oil", "Avocado Oil", "bottle", 365, T, ["avocado oil", "cold pressed avocado oil", "high heat oil"]),
    ("sesame_oil", "Sesame Oil", "bottle", 365, T, ["sesame oil", "toasted sesame oil", "dark sesame oil"]),
    ("peanut_oil", "Peanut Oil", "bottle", 365, T, ["peanut oil", "groundnut oil", "frying oil"]),
    ("cooking_spray", "Cooking Spray", "can", 730, T, ["cooking spray", "nonstick spray", "pam", "oil spray"]),
    ("white_vinegar", "White Vinegar", "bottle", 1095, T, ["white vinegar", "distilled vinegar", "distilled white vinegar"]),
    ("apple_cider_vinegar", "Apple Cider Vinegar", "bottle", 1095, T, ["apple cider vinegar", "acv", "cider vinegar"]),
    ("balsamic_vinegar", "Balsamic Vinegar", "bottle", 1095, T, ["balsamic vinegar", "balsamic", "aged balsamic"]),
    ("red_wine_vinegar", "Red Wine Vinegar", "bottle", 1095, T, ["red wine vinegar", "wine vinegar", "red vinegar"]),
    ("rice_vinegar", "Rice Vinegar", "bottle", 1095, T, ["rice vinegar", "rice wine vinegar", "seasoned rice vinegar"]),
]

# ---------------------------------------------------------------------------
# SPICE
# ---------------------------------------------------------------------------
SPICE = [
    ("salt", "Salt", "container", 1825, T, ["salt", "table salt", "iodized salt", "fine salt"]),
    ("kosher_salt", "Kosher Salt", "box", 1825, T, ["kosher salt", "coarse salt", "diamond crystal salt"]),
    ("sea_salt", "Sea Salt", "container", 1825, T, ["sea salt", "flaky sea salt", "coarse sea salt"]),
    ("black_pepper", "Black Pepper", "container", 1095, T, ["black pepper", "ground black pepper", "cracked pepper", "peppercorns"]),
    ("white_pepper", "White Pepper", "jar", 1095, T, ["white pepper", "ground white pepper", "white peppercorn"]),
    ("garlic_powder", "Garlic Powder", "jar", 1095, T, ["garlic powder", "granulated garlic", "garlic granules"]),
    ("onion_powder", "Onion Powder", "jar", 1095, T, ["onion powder", "granulated onion", "dried onion powder"]),
    ("paprika", "Paprika", "jar", 1095, T, ["paprika", "sweet paprika", "hungarian paprika"]),
    ("smoked_paprika", "Smoked Paprika", "jar", 1095, T, ["smoked paprika", "pimenton", "spanish paprika"]),
    ("ground_cumin", "Ground Cumin", "jar", 1095, T, ["cumin", "ground cumin", "cumin powder"]),
    ("chili_powder", "Chili Powder", "jar", 1095, T, ["chili powder", "chile powder", "chilli powder"]),
    ("cayenne_pepper", "Cayenne Pepper", "jar", 1095, T, ["cayenne", "cayenne pepper", "ground cayenne", "red pepper"]),
    ("crushed_red_pepper", "Crushed Red Pepper", "jar", 1095, T, ["crushed red pepper", "red pepper flakes", "chili flakes", "pepper flakes"]),
    ("dried_oregano", "Dried Oregano", "jar", 1095, T, ["oregano", "dried oregano", "italian oregano"]),
    ("dried_basil", "Dried Basil", "jar", 1095, T, ["dried basil", "basil", "basil leaves"]),
    ("dried_thyme", "Dried Thyme", "jar", 1095, T, ["dried thyme", "thyme", "thyme leaves"]),
    ("dried_rosemary", "Dried Rosemary", "jar", 1095, T, ["dried rosemary", "rosemary", "rosemary leaves"]),
    ("dried_parsley", "Dried Parsley", "jar", 1095, T, ["dried parsley", "parsley flakes", "parsley"]),
    ("dried_dill", "Dried Dill", "jar", 1095, T, ["dried dill", "dill weed", "dill"]),
    ("dried_sage", "Dried Sage", "jar", 1095, T, ["dried sage", "rubbed sage", "sage"]),
    ("bay_leaves", "Bay Leaves", "jar", 1095, T, ["bay leaves", "bay leaf", "laurel leaves"]),
    ("ground_cinnamon", "Ground Cinnamon", "jar", 1095, T, ["cinnamon", "ground cinnamon", "cinnamon powder"]),
    ("ground_nutmeg", "Ground Nutmeg", "jar", 1095, T, ["nutmeg", "ground nutmeg", "grated nutmeg"]),
    ("ground_ginger", "Ground Ginger", "jar", 1095, T, ["ground ginger", "ginger powder", "dried ginger"]),
    ("ground_turmeric", "Ground Turmeric", "jar", 1095, T, ["turmeric", "ground turmeric", "turmeric powder"]),
    ("curry_powder", "Curry Powder", "jar", 1095, T, ["curry powder", "yellow curry powder", "madras curry powder"]),
    ("ground_cloves", "Ground Cloves", "jar", 1095, T, ["cloves", "ground cloves", "whole cloves"]),
    ("ground_allspice", "Ground Allspice", "jar", 1095, T, ["allspice", "ground allspice", "pimento spice"]),
    ("ground_coriander", "Ground Coriander", "jar", 1095, T, ["coriander", "ground coriander", "coriander powder"]),
    ("cardamom", "Cardamom", "jar", 1095, T, ["cardamom", "ground cardamom", "green cardamom"]),
    ("fennel_seed", "Fennel Seed", "jar", 1095, T, ["fennel seed", "fennel seeds", "whole fennel"]),
    ("mustard_seed", "Mustard Seed", "jar", 1095, T, ["mustard seed", "mustard seeds", "yellow mustard seed"]),
    ("celery_salt", "Celery Salt", "jar", 1095, T, ["celery salt", "celery seasoning", "celery seasoned salt"]),
    ("seasoned_salt", "Seasoned Salt", "jar", 1095, T, ["seasoned salt", "season salt", "lawrys seasoned salt"]),
    ("italian_seasoning", "Italian Seasoning", "jar", 1095, T, ["italian seasoning", "italian herb blend", "italian herbs"]),
    ("taco_seasoning", "Taco Seasoning", "packet", 730, T, ["taco seasoning", "taco spice mix", "taco mix"]),
    ("poultry_seasoning", "Poultry Seasoning", "jar", 1095, T, ["poultry seasoning", "chicken seasoning", "sage seasoning"]),
    ("old_bay_seasoning", "Old Bay Seasoning", "jar", 1095, T, ["old bay", "old bay seasoning", "seafood seasoning"]),
    ("cajun_seasoning", "Cajun Seasoning", "jar", 1095, T, ["cajun seasoning", "creole seasoning", "blackening seasoning"]),
    ("everything_bagel_seasoning", "Everything Bagel Seasoning", "jar", 1095, T, ["everything bagel seasoning", "everything seasoning", "bagel seasoning"]),
    ("garam_masala", "Garam Masala", "jar", 1095, T, ["garam masala", "indian spice blend", "masala"]),
    ("chinese_five_spice", "Chinese Five Spice", "jar", 1095, T, ["five spice", "chinese five spice", "5 spice powder"]),
    ("chipotle_powder", "Chipotle Powder", "jar", 1095, T, ["chipotle powder", "ground chipotle", "smoked chile powder"]),
    ("saffron", "Saffron", "jar", 1095, T, ["saffron", "saffron threads", "spanish saffron"]),
    ("cream_of_tartar", "Cream of Tartar", "jar", 1095, T, ["cream of tartar", "tartaric acid", "potassium bitartrate"]),
    ("chili_flakes", "Aleppo Pepper", "jar", 1095, T, ["aleppo pepper", "aleppo chili", "turkish red pepper"]),
    ("ground_mustard", "Ground Mustard", "jar", 1095, T, ["ground mustard", "dry mustard", "mustard powder"]),
    ("garlic_salt", "Garlic Salt", "jar", 1095, T, ["garlic salt", "garlic seasoning salt", "seasoned garlic salt"]),
]

# ---------------------------------------------------------------------------
# BAKERY
# ---------------------------------------------------------------------------
BAKERY = [
    ("white_bread", "White Bread", "loaf", 7, F, ["white bread", "sandwich bread", "loaf of bread", "sliced bread"]),
    ("whole_wheat_bread", "Whole Wheat Bread", "loaf", 7, F, ["whole wheat bread", "wheat bread", "whole grain bread", "brown bread"]),
    ("sourdough_bread", "Sourdough Bread", "loaf", 6, F, ["sourdough", "sourdough bread", "sourdough loaf"]),
    ("italian_bread", "Italian Bread", "loaf", 4, F, ["italian bread", "italian loaf", "crusty bread"]),
    ("french_baguette", "French Baguette", "each", 3, F, ["baguette", "french baguette", "french bread", "french loaf"]),
    ("bagel", "Bagels", "package", 7, F, ["bagels", "plain bagels", "everything bagels", "bagel"]),
    ("english_muffin", "English Muffins", "package", 10, F, ["english muffins", "english muffin", "breakfast muffins"]),
    ("hamburger_buns", "Hamburger Buns", "package", 7, F, ["hamburger buns", "burger buns", "sesame buns", "hamburger rolls"]),
    ("hot_dog_buns", "Hot Dog Buns", "package", 7, F, ["hot dog buns", "hot dog rolls", "frankfurter buns"]),
    ("dinner_rolls", "Dinner Rolls", "package", 7, F, ["dinner rolls", "soft rolls", "yeast rolls", "rolls"]),
    ("croissant", "Croissants", "package", 5, F, ["croissants", "butter croissants", "croissant"]),
    ("blueberry_muffin", "Blueberry Muffins", "package", 5, F, ["blueberry muffins", "muffins", "bakery muffins"]),
    ("flour_tortilla", "Flour Tortillas", "package", 30, F, ["flour tortillas", "tortillas", "burrito wraps", "soft tortillas"]),
    ("corn_tortilla", "Corn Tortillas", "package", 21, F, ["corn tortillas", "tortillas", "street taco tortillas"]),
    ("pita_bread", "Pita Bread", "package", 7, F, ["pita bread", "pita", "pita pockets", "flatbread"]),
    ("naan", "Naan", "package", 10, F, ["naan", "naan bread", "indian flatbread", "garlic naan"]),
    ("ciabatta", "Ciabatta", "each", 4, F, ["ciabatta", "ciabatta bread", "ciabatta rolls"]),
    ("rye_bread", "Rye Bread", "loaf", 7, F, ["rye bread", "marble rye", "pumpernickel", "jewish rye"]),
    ("brioche_bun", "Brioche Buns", "package", 7, F, ["brioche buns", "brioche rolls", "brioche"]),
    ("donut", "Donuts", "package", 4, F, ["donuts", "doughnuts", "glazed donuts", "donut"]),
]

# ---------------------------------------------------------------------------
# FROZEN
# ---------------------------------------------------------------------------
FROZEN = [
    ("frozen_peas", "Frozen Peas", "bag", 300, F, ["frozen peas", "sweet peas", "green peas", "bag of peas"]),
    ("frozen_corn", "Frozen Corn", "bag", 300, F, ["frozen corn", "sweet corn", "corn kernels"]),
    ("frozen_broccoli", "Frozen Broccoli", "bag", 300, F, ["frozen broccoli", "broccoli florets", "broccoli cuts"]),
    ("frozen_spinach", "Frozen Spinach", "box", 300, F, ["frozen spinach", "chopped spinach", "spinach"]),
    ("frozen_mixed_vegetables", "Frozen Mixed Vegetables", "bag", 300, F, ["frozen mixed vegetables", "mixed veggies", "vegetable medley", "mixed vegetables"]),
    ("frozen_strawberries", "Frozen Strawberries", "bag", 300, F, ["frozen strawberries", "frozen berries", "sliced strawberries"]),
    ("frozen_blueberries", "Frozen Blueberries", "bag", 300, F, ["frozen blueberries", "frozen berries", "wild blueberries"]),
    ("frozen_pizza", "Frozen Pizza", "each", 270, F, ["frozen pizza", "pizza", "digiorno", "frozen cheese pizza"]),
    ("french_fries", "French Fries", "bag", 300, F, ["french fries", "frozen fries", "crinkle fries", "fries"]),
    ("chicken_nuggets", "Chicken Nuggets", "bag", 270, F, ["chicken nuggets", "nuggets", "frozen nuggets", "dino nuggets"]),
    ("fish_sticks", "Fish Sticks", "box", 270, F, ["fish sticks", "fish fingers", "breaded fish sticks"]),
    ("frozen_waffles", "Frozen Waffles", "box", 270, F, ["frozen waffles", "eggo waffles", "toaster waffles", "waffles"]),
    ("ice_cream", "Ice Cream", "container", 180, F, ["ice cream", "vanilla ice cream", "pint of ice cream", "frozen dessert"]),
    ("frozen_yogurt", "Frozen Yogurt", "container", 180, F, ["frozen yogurt", "froyo", "fro-yo"]),
    ("popsicles", "Popsicles", "box", 300, F, ["popsicles", "ice pops", "freeze pops", "fruit pops"]),
    ("frozen_shrimp", "Frozen Shrimp", "bag", 300, F, ["frozen shrimp", "raw shrimp", "cooked shrimp", "shrimp"]),
    ("frozen_edamame", "Frozen Edamame", "bag", 300, F, ["frozen edamame", "edamame", "soybeans in pod"]),
    ("frozen_dumplings", "Frozen Dumplings", "bag", 270, F, ["frozen dumplings", "potstickers", "gyoza", "dumplings"]),
    ("frozen_burrito", "Frozen Burrito", "each", 270, F, ["frozen burrito", "bean burrito", "burrito"]),
    ("frozen_lasagna", "Frozen Lasagna", "each", 270, F, ["frozen lasagna", "family lasagna", "lasagna"]),
    ("frozen_hash_browns", "Frozen Hash Browns", "bag", 300, F, ["frozen hash browns", "shredded hash browns", "hash browns", "potato patties"]),
    ("frozen_meatballs", "Frozen Meatballs", "bag", 270, F, ["frozen meatballs", "meatballs", "italian meatballs"]),
    ("frozen_garlic_bread", "Frozen Garlic Bread", "each", 270, F, ["frozen garlic bread", "garlic bread", "garlic toast"]),
    ("frozen_pie_crust", "Frozen Pie Crust", "package", 300, T, ["frozen pie crust", "pie shells", "pie crust", "pastry shell"]),
    ("frozen_berries", "Frozen Mixed Berries", "bag", 300, F, ["frozen mixed berries", "berry blend", "frozen fruit", "mixed berries"]),
]

# ---------------------------------------------------------------------------
# SNACK
# ---------------------------------------------------------------------------
SNACK = [
    ("potato_chips", "Potato Chips", "bag", 60, F, ["potato chips", "chips", "kettle chips", "crisps"]),
    ("tortilla_chips", "Tortilla Chips", "bag", 90, F, ["tortilla chips", "corn chips", "nacho chips", "restaurant chips"]),
    ("pretzels", "Pretzels", "bag", 120, F, ["pretzels", "hard pretzels", "pretzel twists", "pretzel sticks"]),
    ("popcorn", "Popcorn", "bag", 180, T, ["popcorn", "popping corn", "kernels", "popcorn kernels"]),
    ("microwave_popcorn", "Microwave Popcorn", "box", 270, F, ["microwave popcorn", "butter popcorn", "popcorn bags"]),
    ("saltine_crackers", "Saltine Crackers", "box", 180, T, ["saltine crackers", "saltines", "soda crackers", "crackers"]),
    ("graham_crackers", "Graham Crackers", "box", 180, F, ["graham crackers", "grahams", "honey grahams"]),
    ("cheese_crackers", "Cheese Crackers", "box", 150, F, ["cheese crackers", "cheez-its", "cheddar crackers"]),
    ("wheat_crackers", "Wheat Crackers", "box", 180, F, ["wheat crackers", "wheat thins", "whole grain crackers"]),
    ("peanuts", "Peanuts", "container", 180, T, ["peanuts", "roasted peanuts", "salted peanuts", "dry roasted peanuts"]),
    ("almonds", "Almonds", "bag", 270, T, ["almonds", "raw almonds", "roasted almonds", "whole almonds"]),
    ("cashews", "Cashews", "container", 180, T, ["cashews", "roasted cashews", "salted cashews", "whole cashews"]),
    ("walnuts", "Walnuts", "bag", 180, T, ["walnuts", "walnut halves", "chopped walnuts", "english walnuts"]),
    ("mixed_nuts", "Mixed Nuts", "container", 180, T, ["mixed nuts", "deluxe mixed nuts", "assorted nuts", "nut mix"]),
    ("trail_mix", "Trail Mix", "bag", 120, F, ["trail mix", "gorp", "nut and fruit mix"]),
    ("granola_bar", "Granola Bars", "box", 180, F, ["granola bars", "granola bar", "chewy granola bars", "oat bars"]),
    ("protein_bar", "Protein Bars", "box", 270, F, ["protein bars", "protein bar", "energy bars", "clif bars"]),
    ("chocolate_chip_cookies", "Chocolate Chip Cookies", "package", 90, F, ["chocolate chip cookies", "cookies", "chips ahoy"]),
    ("sandwich_cookies", "Sandwich Cookies", "package", 180, F, ["sandwich cookies", "oreos", "cream cookies", "chocolate sandwich cookies"]),
    ("candy_bar", "Candy Bar", "each", 270, F, ["candy bar", "chocolate bar", "snickers", "chocolate candy"]),
    ("gummy_candy", "Gummy Candy", "bag", 270, F, ["gummy candy", "gummy bears", "gummies", "fruit gummies"]),
    ("raisins", "Raisins", "container", 365, T, ["raisins", "golden raisins", "sun-maid raisins", "dried grapes"]),
    ("dried_cranberries", "Dried Cranberries", "bag", 365, T, ["dried cranberries", "craisins", "sweetened cranberries"]),
    ("beef_jerky", "Beef Jerky", "bag", 365, F, ["beef jerky", "jerky", "dried beef", "meat snack"]),
    ("rice_cakes", "Rice Cakes", "package", 120, F, ["rice cakes", "brown rice cakes", "puffed rice cakes"]),
    ("fruit_snacks", "Fruit Snacks", "box", 270, F, ["fruit snacks", "gushers", "fruit gummies", "welchs fruit snacks"]),
]

# ---------------------------------------------------------------------------
# BEVERAGE
# ---------------------------------------------------------------------------
BEVERAGE = [
    ("orange_juice", "Orange Juice", "carton", 21, F, ["orange juice", "oj", "not from concentrate orange juice", "fresh orange juice"]),
    ("apple_juice", "Apple Juice", "bottle", 90, F, ["apple juice", "apple cider", "100% apple juice"]),
    ("cranberry_juice", "Cranberry Juice", "bottle", 120, F, ["cranberry juice", "cranberry cocktail", "cran juice"]),
    ("grape_juice", "Grape Juice", "bottle", 120, F, ["grape juice", "welchs grape juice", "purple grape juice"]),
    ("lemonade", "Lemonade", "bottle", 30, F, ["lemonade", "pink lemonade", "fresh lemonade"]),
    ("ground_coffee", "Ground Coffee", "bag", 180, T, ["ground coffee", "coffee", "drip coffee", "medium roast coffee"]),
    ("coffee_beans", "Whole Bean Coffee", "bag", 270, T, ["coffee beans", "whole bean coffee", "espresso beans", "coffee"]),
    ("instant_coffee", "Instant Coffee", "jar", 545, T, ["instant coffee", "coffee crystals", "freeze dried coffee"]),
    ("black_tea", "Black Tea", "box", 545, T, ["black tea", "tea bags", "english breakfast tea", "tea"]),
    ("green_tea", "Green Tea", "box", 545, T, ["green tea", "green tea bags", "sencha", "tea"]),
    ("herbal_tea", "Herbal Tea", "box", 545, T, ["herbal tea", "chamomile tea", "peppermint tea", "caffeine free tea"]),
    ("cola", "Cola", "bottle", 270, F, ["cola", "coke", "pepsi", "soda"]),
    ("diet_cola", "Diet Cola", "bottle", 270, F, ["diet cola", "diet coke", "diet pepsi", "diet soda"]),
    ("lemon_lime_soda", "Lemon-Lime Soda", "bottle", 270, F, ["lemon lime soda", "sprite", "7up", "sierra mist"]),
    ("ginger_ale", "Ginger Ale", "bottle", 270, F, ["ginger ale", "canada dry", "schweppes ginger ale"]),
    ("root_beer", "Root Beer", "bottle", 270, F, ["root beer", "a&w root beer", "barqs"]),
    ("sparkling_water", "Sparkling Water", "can", 365, F, ["sparkling water", "seltzer", "lacroix", "carbonated water"]),
    ("bottled_water", "Bottled Water", "case", 365, F, ["bottled water", "spring water", "purified water", "water"]),
    ("sports_drink", "Sports Drink", "bottle", 270, F, ["sports drink", "gatorade", "powerade", "electrolyte drink"]),
    ("energy_drink", "Energy Drink", "can", 365, F, ["energy drink", "red bull", "monster", "caffeine drink"]),
    ("coconut_water", "Coconut Water", "carton", 270, F, ["coconut water", "vita coco", "coco water"]),
    ("club_soda", "Club Soda", "bottle", 365, F, ["club soda", "soda water", "carbonated water", "seltzer water"]),
    ("hot_chocolate_mix", "Hot Chocolate Mix", "box", 365, T, ["hot chocolate mix", "hot cocoa mix", "cocoa mix", "swiss miss"]),
    ("iced_tea", "Iced Tea", "bottle", 120, F, ["iced tea", "sweet tea", "unsweetened iced tea", "lipton iced tea"]),
    ("almond_milk_shelf", "Shelf-Stable Almond Milk", "carton", 270, T, ["shelf stable almond milk", "boxed almond milk", "almond beverage"]),
]

# ---------------------------------------------------------------------------
# SAUCE
# ---------------------------------------------------------------------------
SAUCE = [
    ("marinara_sauce", "Marinara Sauce", "jar", 365, T, ["marinara", "marinara sauce", "pasta sauce", "spaghetti sauce"]),
    ("alfredo_sauce", "Alfredo Sauce", "jar", 365, T, ["alfredo sauce", "alfredo", "white pasta sauce", "creamy alfredo"]),
    ("vodka_sauce", "Vodka Sauce", "jar", 365, T, ["vodka sauce", "pink sauce", "tomato cream sauce"]),
    ("pesto", "Pesto", "jar", 120, T, ["pesto", "basil pesto", "pesto sauce", "genovese pesto"]),
    ("soy_sauce", "Soy Sauce", "bottle", 730, T, ["soy sauce", "shoyu", "light soy sauce", "low sodium soy sauce"]),
    ("teriyaki_sauce", "Teriyaki Sauce", "bottle", 545, T, ["teriyaki sauce", "teriyaki", "teriyaki glaze"]),
    ("hoisin_sauce", "Hoisin Sauce", "bottle", 545, T, ["hoisin sauce", "hoisin", "chinese bbq sauce"]),
    ("oyster_sauce", "Oyster Sauce", "bottle", 545, T, ["oyster sauce", "oyster flavored sauce", "asian oyster sauce"]),
    ("fish_sauce", "Fish Sauce", "bottle", 730, T, ["fish sauce", "nam pla", "nuoc mam", "asian fish sauce"]),
    ("worcestershire_sauce", "Worcestershire Sauce", "bottle", 1095, T, ["worcestershire sauce", "worcestershire", "lea perrins"]),
    ("hot_sauce", "Hot Sauce", "bottle", 730, T, ["hot sauce", "franks red hot", "tabasco", "louisiana hot sauce"]),
    ("sriracha", "Sriracha", "bottle", 730, T, ["sriracha", "rooster sauce", "chili sauce", "sriracha hot sauce"]),
    ("enchilada_sauce", "Enchilada Sauce", "can", 545, T, ["enchilada sauce", "red enchilada sauce", "green enchilada sauce"]),
    ("pizza_sauce", "Pizza Sauce", "jar", 365, T, ["pizza sauce", "pizza tomato sauce", "pizza topping sauce"]),
    ("gravy", "Gravy", "jar", 365, F, ["gravy", "brown gravy", "turkey gravy", "chicken gravy"]),
    ("buffalo_sauce", "Buffalo Sauce", "bottle", 365, T, ["buffalo sauce", "buffalo wing sauce", "wing sauce"]),
    ("sweet_and_sour_sauce", "Sweet and Sour Sauce", "bottle", 365, T, ["sweet and sour sauce", "sweet & sour sauce", "duck sauce"]),
    ("chili_garlic_sauce", "Chili Garlic Sauce", "jar", 545, T, ["chili garlic sauce", "sambal oelek", "garlic chili sauce"]),
    ("tahini", "Tahini", "jar", 365, T, ["tahini", "sesame paste", "tahini paste"]),
    ("gochujang", "Gochujang", "container", 545, T, ["gochujang", "korean chili paste", "red pepper paste"]),
]

CATEGORIES = {
    "produce": PRODUCE,
    "meat": MEAT,
    "seafood": SEAFOOD,
    "dairy": DAIRY,
    "eggs": EGGS,
    "grain": GRAIN,
    "canned": CANNED,
    "condiment": CONDIMENT,
    "oil_vinegar": OIL_VINEGAR,
    "spice": SPICE,
    "bakery": BAKERY,
    "frozen": FROZEN,
    "snack": SNACK,
    "beverage": BEVERAGE,
    "sauce": SAUCE,
}


def build_rows() -> list[dict]:
    rows: dict[str, dict] = {}
    for category, items in CATEGORIES.items():
        for canonical_name, display_name, unit, shelf, staple, aliases in items:
            rows[canonical_name] = {
                "canonical_name": canonical_name,
                "display_name": display_name,
                "category": category,
                "typical_unit": unit,
                "shelf_life_days": shelf,
                "is_pantry_staple": staple,
                "common_aliases": aliases,
            }
    return list(rows.values())


async def main() -> None:
    rows = build_rows()
    async with AsyncSessionLocal() as session:
        stmt = insert(IngredientMaster).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["canonical_name"],
            set_={
                "display_name": stmt.excluded.display_name,
                "category": stmt.excluded.category,
                "typical_unit": stmt.excluded.typical_unit,
                "shelf_life_days": stmt.excluded.shelf_life_days,
                "is_pantry_staple": stmt.excluded.is_pantry_staple,
                "common_aliases": stmt.excluded.common_aliases,
            },
        )
        await session.execute(stmt)
        await session.commit()

        count = await session.scalar(
            select(func.count()).select_from(IngredientMaster)
        )
    print(f"ingredients={count} (seed rows prepared: {len(rows)})")


if __name__ == "__main__":
    asyncio.run(main())
