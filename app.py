import streamlit as st
import requests
import json
from streamlit.errors import StreamlitSecretNotFoundError


KAKAO_API_BASE_URL = "https://dapi.kakao.com/v2/local/search"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
SUPABASE_TABLE_URL = "/rest/v1/favorites"


def search_nearby_restaurants(location, radius, sort):
    api_key = st.secrets["KAKAO_REST_API_KEY"]
    headers = {"Authorization": f"KakaoAK {api_key}"}

    keyword_response = requests.get(
        f"{KAKAO_API_BASE_URL}/keyword.json",
        headers=headers,
        params={"query": location, "size": 1},
        timeout=10,
    )
    keyword_response.raise_for_status()
    places = keyword_response.json().get("documents", [])
    if not places:
        return None

    category_response = requests.get(
        f"{KAKAO_API_BASE_URL}/category.json",
        headers=headers,
        params={
            "category_group_code": "FD6",
            "x": places[0]["x"],
            "y": places[0]["y"],
            "radius": radius,
            "sort": sort,
        },
        timeout=10,
    )
    category_response.raise_for_status()
    return category_response.json().get("documents", [])


def get_food_type(category_name):
    categories = [category.strip() for category in category_name.split(">")]
    return categories[1] if len(categories) > 1 else categories[0]


def get_ai_recommendations(restaurants, situation):
    api_key = st.secrets["OPENAI_API_KEY"]
    restaurant_data = [
        {
            "id": str(restaurant["id"]),
            "name": restaurant["place_name"],
            "category": restaurant["category_name"],
            "distance": restaurant["distance"],
        }
        for restaurant in restaurants
    ]
    system_prompt = """
페르소나: 대학생의 점심 고민을 덜어주는 친절한 맛집 안내자
형식 규칙: 추천은 3곳, 이유는 한 줄, 존댓말
제약 규칙: 전달한 목록 안에서만 고르고, 메뉴·가격·맛·분위기는 추측하지 말 것
출력은 {"recommendations": [{"id": "...", "reason": "..."}]} 형태의 JSON으로만 답할 것
""".strip()
    user_prompt = json.dumps(
        {"situation": situation, "restaurants": restaurant_data},
        ensure_ascii=False,
    )
    response = requests.post(
        OPENAI_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=30,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    recommendations = json.loads(content).get("recommendations", [])
    allowed_ids = {str(restaurant["id"]) for restaurant in restaurants}
    valid_recommendations = [
        recommendation
        for recommendation in recommendations
        if (
            str(recommendation.get("id")) in allowed_ids
            and isinstance(recommendation.get("reason"), str)
        )
    ]
    if len(valid_recommendations) < 3:
        raise ValueError("AI 추천 결과가 올바르지 않습니다.")
    return valid_recommendations[:3]


def get_supabase_connection():
    base_url = st.secrets["SUPABASE_URL"].rstrip("/")
    api_key = st.secrets["SUPABASE_KEY"]
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    return f"{base_url}{SUPABASE_TABLE_URL}", headers


def save_favorite(restaurant, nickname, memo):
    nickname = nickname.strip()
    if not nickname:
        raise ValueError("별칭을 입력해주세요")

    table_url, headers = get_supabase_connection()
    response = requests.post(
        table_url,
        headers={**headers, "Prefer": "return=minimal"},
        json={
            "place_id": str(restaurant["id"]),
            "place_url": restaurant["place_url"],
            "nickname": nickname,
            "memo": memo,
        },
        timeout=10,
    )
    response.raise_for_status()


def load_favorites():
    table_url, headers = get_supabase_connection()
    response = requests.get(
        table_url,
        headers=headers,
        params={
            "select": "id,place_id,place_url,nickname,memo,created_at",
            "order": "created_at.desc",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def delete_favorite(favorite_id):
    table_url, headers = get_supabase_connection()
    response = requests.delete(
        table_url,
        headers={**headers, "Prefer": "return=minimal"},
        params={"id": f"eq.{favorite_id}"},
        timeout=10,
    )
    response.raise_for_status()


def update_favorite_memo(favorite_id, memo):
    table_url, headers = get_supabase_connection()
    response = requests.patch(
        table_url,
        headers={**headers, "Prefer": "return=minimal"},
        params={"id": f"eq.{favorite_id}"},
        json={"memo": memo},
        timeout=10,
    )
    response.raise_for_status()


st.set_page_config(page_title="AI 맛집 추천 서비스", page_icon="🍽️")

st.title("AI 맛집 추천 서비스")
st.write("원하는 위치를 입력하면 주변 맛집을 찾아드려요.")

if "restaurants" not in st.session_state:
    st.session_state.restaurants = None
if "last_location" not in st.session_state:
    st.session_state.last_location = ""
if "search_error" not in st.session_state:
    st.session_state.search_error = ""
if "ai_recommendations" not in st.session_state:
    st.session_state.ai_recommendations = None
if "ai_error" not in st.session_state:
    st.session_state.ai_error = ""
if "editing_favorite_id" not in st.session_state:
    st.session_state.editing_favorite_id = None

available_food_types = sorted(
    {
        get_food_type(restaurant["category_name"])
        for restaurant in (st.session_state.restaurants or [])
    }
)

with st.sidebar:
    st.header("검색 필터")
    food_type = st.selectbox("음식 종류", ["전체", *available_food_types])
    radius = st.slider("반경", min_value=100, max_value=2000, value=500, step=100)
    sort_label = st.selectbox("정렬", ["거리순", "정확도순"])
    sort = "distance" if sort_label == "거리순" else "accuracy"

search_tab, favorites_tab = st.tabs(["검색", "즐겨찾기"])

with search_tab:
    with st.form("search_form"):
        location = st.text_input("위치", placeholder="예: 강남역, 부산 해운대")
        submitted = st.form_submit_button("검색")

    if submitted:
        location = location.strip()
        if location:
            st.session_state.ai_recommendations = None
            st.session_state.ai_error = ""
            try:
                with st.spinner("주변 음식점을 검색하고 있습니다..."):
                    st.session_state.restaurants = search_nearby_restaurants(
                        location, radius, sort
                    )
                st.session_state.last_location = location
                st.session_state.search_error = ""
                st.rerun()
            except (KeyError, StreamlitSecretNotFoundError):
                st.session_state.search_error = "카카오 REST API 키가 설정되지 않았습니다."
                st.rerun()
            except requests.RequestException:
                st.session_state.search_error = "카카오 장소 검색 중 오류가 발생했습니다."
                st.rerun()
        else:
            st.warning("위치를 입력해주세요")

    restaurants = st.session_state.restaurants
    filtered_restaurants = [
        restaurant
        for restaurant in (restaurants or [])
        if food_type == "전체"
        or get_food_type(restaurant["category_name"]) == food_type
    ]

    today_situation = st.text_input(
        "오늘 상황",
        placeholder="예: 비 오는 날, 혼자, 빨리",
    )
    ai_submitted = st.button("AI 추천")

    if ai_submitted:
        if not filtered_restaurants:
            st.warning("먼저 검색해주세요")
        else:
            try:
                with st.spinner("AI가 맛집을 추천하고 있습니다..."):
                    st.session_state.ai_recommendations = get_ai_recommendations(
                        filtered_restaurants, today_situation.strip()
                    )
                st.session_state.ai_error = ""
            except (KeyError, StreamlitSecretNotFoundError):
                st.session_state.ai_error = "OpenAI API 키가 설정되지 않았습니다."
                st.session_state.ai_recommendations = None
            except (requests.RequestException, ValueError, json.JSONDecodeError):
                st.session_state.ai_error = "AI 추천 중 오류가 발생했습니다."
                st.session_state.ai_recommendations = None

    if st.session_state.search_error:
        st.error(st.session_state.search_error)
    elif st.session_state.last_location:
        st.success(f"{st.session_state.last_location} 주변을 검색합니다")
        if restaurants is None:
            st.error("입력한 장소를 찾지 못했습니다.")
        else:
            if st.session_state.ai_error:
                st.error(st.session_state.ai_error)

            if st.session_state.ai_recommendations:
                st.subheader("AI 추천")
                restaurants_by_id = {
                    str(restaurant["id"]): restaurant for restaurant in restaurants
                }
                for recommendation in st.session_state.ai_recommendations:
                    restaurant = restaurants_by_id.get(str(recommendation["id"]))
                    if restaurant is None:
                        continue
                    with st.container(border=True):
                        st.markdown(
                            f"### [{restaurant['place_name']}]({restaurant['place_url']})"
                        )
                        st.write(f"거리: {restaurant['distance']}m")
                        st.write(f"추천 이유: {recommendation['reason']}")

            if not filtered_restaurants:
                st.warning("조건에 맞는 식당이 없어요")
            else:
                st.subheader("주변 음식점")
                for restaurant in filtered_restaurants:
                    with st.container(border=True):
                        st.markdown(
                            f"**[{restaurant['place_name']}]({restaurant['place_url']})**"
                        )
                        st.write(
                            f"{restaurant['category_name']} · "
                            f"{restaurant['distance']}m"
                        )
                        with st.form(f"favorite_form_{restaurant['id']}"):
                            nickname = st.text_input(
                                "별칭",
                                key=f"favorite_nickname_{restaurant['id']}",
                            )
                            memo = st.text_input(
                                "메모",
                                key=f"favorite_memo_{restaurant['id']}",
                            )
                            save_submitted = st.form_submit_button("저장")
                        if save_submitted:
                            if not nickname.strip():
                                st.warning("별칭을 입력해주세요.")
                            else:
                                try:
                                    save_favorite(
                                        restaurant, nickname.strip(), memo.strip()
                                    )
                                    st.success("즐겨찾기에 저장했습니다.")
                                except (KeyError, StreamlitSecretNotFoundError):
                                    st.error("Supabase 설정이 없습니다.")
                                except ValueError as error:
                                    st.warning(str(error))
                                except requests.RequestException:
                                    st.error("즐겨찾기 저장 중 오류가 발생했습니다.")

with favorites_tab:
    st.subheader("즐겨찾기")
    try:
        favorites = load_favorites()
        if not favorites:
            st.info("저장된 즐겨찾기가 없습니다.")
        else:
            for favorite in favorites:
                with st.container(border=True):
                    restaurant_lookup = {
                        str(restaurant["id"]): restaurant
                        for restaurant in (st.session_state.restaurants or [])
                    }
                    restaurant = restaurant_lookup.get(str(favorite["place_id"]))
                    if restaurant:
                        st.markdown(f"### {restaurant['place_name']}")
                        st.write(f"별칭: {favorite['nickname']}")
                    else:
                        st.markdown(f"### {favorite['nickname']}")
                    if st.session_state.editing_favorite_id == favorite["id"]:
                        with st.form(f"edit_favorite_form_{favorite['id']}"):
                            edited_memo = st.text_area(
                                "메모",
                                value=favorite["memo"] or "",
                                key=f"edit_memo_{favorite['id']}",
                            )
                            edit_submitted = st.form_submit_button("저장")
                            cancel_submitted = st.form_submit_button("취소")
                        if edit_submitted:
                            try:
                                update_favorite_memo(
                                    favorite["id"], edited_memo.strip()
                                )
                                st.session_state.editing_favorite_id = None
                                st.rerun()
                            except (KeyError, StreamlitSecretNotFoundError):
                                st.error("Supabase 설정이 없습니다.")
                            except requests.RequestException:
                                st.error("메모 수정 중 오류가 발생했습니다.")
                        elif cancel_submitted:
                            st.session_state.editing_favorite_id = None
                            st.rerun()
                    else:
                        st.write(f"메모: {favorite['memo'] or '-'}")
                        if st.button(
                            "메모 수정",
                            key=f"edit_favorite_{favorite['id']}",
                        ):
                            st.session_state.editing_favorite_id = favorite["id"]
                            st.rerun()
                    st.markdown(f"[카카오맵에서 보기]({favorite['place_url']})")
                    st.caption(f"저장 시각: {favorite['created_at']}")
                    if st.button(
                        "삭제",
                        key=f"delete_favorite_{favorite['id']}",
                    ):
                        try:
                            delete_favorite(favorite["id"])
                            st.rerun()
                        except requests.RequestException:
                            st.error("즐겨찾기 삭제 중 오류가 발생했습니다.")
    except (KeyError, StreamlitSecretNotFoundError):
        st.error("Supabase 설정이 없습니다.")
    except requests.RequestException:
        st.error("즐겨찾기를 불러오는 중 오류가 발생했습니다.")
