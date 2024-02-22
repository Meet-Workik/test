from flask.json import jsonify
import jwt
import flask
from functools import wraps
from Helpers.mysql_connection import close_connection, open_connection
import os

def validate_request(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        jwt_token = flask.request.headers.get('Authorization')
        # data_type = flask.request.headers.get('content-type')
        # if flask.request.method == 'GET':  # Make sure to pass AI API config appropriately
        #   project_id = flask.request.args.get('project_id')
        # elif 'application/json' in data_type:
        #   data = flask.request.get_json()
        #   project_id = data['project_id']
        # else:
        #   project_id = flask.request.form.get("project_id") # Make sure to pass AI API config appropriately

        try:
            if jwt_token:
                try:
                    payload = jwt.decode(jwt_token, os.environ["JWT_SECRET"], algorithms=[os.environ["JWT_ALGORITHM"]])
                    user_id = payload['user_id']      
                    flask.request.json['user_id'] = user_id      
                except (jwt.DecodeError, jwt.ExpiredSignatureError):
                    return {'is_login_fail': True}, 200

                body = flask.request.get_json()
                project_id = body['project_id']

                cnx = open_connection()
                workik_cursor = cnx.cursor(dictionary=True)
                workik_cursor.execute("USE workik")

                project_query = """
                    SELECT COUNT(id) AS project_count
                    FROM projectviews_project
                    WHERE id=%s;
                """
                project_values = (project_id, )
                workik_cursor.execute(project_query, project_values)
                query_result = workik_cursor.fetchone()
                
                project_count = 0
                if query_result:
                    project_count = query_result["project_count"]

                if project_count != 0:
                    connection_obj_query = """
                        SELECT  is_deleted, 
                                count(connection_id) AS connection_count
                        FROM  connection_project_connection
                        WHERE connection_id=%s AND project_id=%s;
                    """
                    connection_obj_values = (user_id, project_id)
                    workik_cursor.execute(connection_obj_query, connection_obj_values)
                    connection_obj_result = workik_cursor.fetchone()

                    connection_is_deleted = True
                    connection_count = 0

                    if connection_obj_result:
                        connection_is_deleted = connection_obj_result["is_deleted"]
                        connection_count = connection_obj_result["connection_count"]

                    is_token_type_personal = False

                    flask.request.json['is_token_type_personal'] = False

                    if connection_count != 0:
                        if connection_is_deleted:
                            return {'success':False, "type": 'user_project_deleted'}, 200
                        else:

                            available_tokens = 0
                            
                            token_type = body['token_type']
                            if "vision" in token_type:
                                required_tokens = 0
                            else:
                              required_tokens = int(token_type.split('_')[-1]) * 1000
                            remaining_token_type_column = f'remaining_{token_type}_tokens'
                            remaining_user_type_column = f'{token_type}_tokens'

                            # Get default plan for the specific token_type
                            select_default_plan_query = """
                                SELECT  pp.order_id AS order_id, 
                                        pp.{} AS {}, 
                                        o.custom_ai_key AS custom_ai_key,
                                        pp.id AS plan_id
                                FROM project_plans as pp  INNER JOIN orders as o 
                                ON pp.order_id = o.id               
                                WHERE pp.user_id = %s AND pp.project_id = %s AND pp.is_default = %s AND pp.end_date > NOW();
                            """.format(remaining_token_type_column, remaining_token_type_column)
                            select_default_plan_values = (user_id, project_id, True)
                            workik_cursor.execute(select_default_plan_query, select_default_plan_values)
                            default_plan = workik_cursor.fetchone()

                            if (default_plan is not None) and (remaining_token_type_column in default_plan):
                                available_tokens = default_plan[remaining_token_type_column]
                            if default_plan is None:
                                select_free_plan_query = """
                                    SELECT * 
                                    FROM user_init_tokens 
                                    WHERE connection_id = %s;
                                """
                                is_token_type_personal = True
                                select_free_plan_values = (user_id,)
                                workik_cursor.execute(select_free_plan_query, select_free_plan_values)
                                default_plan = workik_cursor.fetchone()
                                if not default_plan:
                                  return jsonify({"success": False, "error": "No default plan found", "error_type": "no_default_plan"})
                                
                                if remaining_user_type_column in default_plan:
                                    available_tokens = default_plan[remaining_user_type_column]


                            close_connection(workik_cursor, cnx)
                            # Calculate required tokens
                            flask.request.json['max_tokens'] = required_tokens

                            if 'custom_ai_key' in default_plan and default_plan['custom_ai_key'] is not None and len(default_plan['custom_ai_key']) > 0 :
                                # Add custom_ai_key to the body
                                flask.request.json['ai_key_type'] = "custom"
                                flask.request.json['custom_ai_key'] = default_plan['custom_ai_key']
                                return f(*args, **kwargs)

                            elif available_tokens > required_tokens:
                                flask.request.json['ai_key_type'] = "workik"
                                flask.request.json['updated_remaining_tokens'] = available_tokens - required_tokens
                                if is_token_type_personal:
                                    flask.request.json['is_token_type_personal'] = True
                                else: 
                                    flask.request.json['plan_id'] = default_plan['plan_id']
                                return f(*args, **kwargs)
                            else:
                                if remaining_user_type_column in default_plan:
                                    return jsonify({"success": False, "error": "Token limit exceeded", "error_type": "token_limit"})
                                elif remaining_token_type_column in default_plan and (remaining_token_type_column == 'remaining_ai_3_4_tokens' or remaining_token_type_column == 'remaining_ai_3_16_tokens'):
                                    return jsonify({"success": False, "error": "Plan Token limit exceeded", "error_type": "token_limit_plan"})
                                else:
                                    return jsonify({"success": False, "error": "Custom Token Missing", "error_type": "custom_token_missing"})
                    else:
                        close_connection(workik_cursor, cnx)
                        return {'success':False, "type": 'invalid_user_connection', "error": "User connection not found"}, 200
                else:
                    close_connection(workik_cursor, cnx)
                    return {'success': False, "type": 'invalid_project', "error": "Project not found"}, 200
            else: 
                return {'is_login_fail': True}, 200
        except Exception as e:
            return jsonify({"error": e})

    return wrapper